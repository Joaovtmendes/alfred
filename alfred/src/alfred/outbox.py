# ruff: noqa: E501
"""V2-18 — what the bot sent, and what Meta says happened to it.

* ``apply_status`` — one entry of the Meta ``statuses[]`` webhook payload updates the matching
  outbound ``Message`` (sent → delivered → read, or failed with Meta's error). Statuses can
  arrive out of order ("read" before "delivered"): the state never goes backwards.
* ``record_outbound`` — proactive sends (cron) have no handler run to store them; this does.
* ``handle_outbox_command`` — "o que você me enviou hoje" / "lembretes que mandou": only the
  member's own messages.
* ``purge_old`` — outbound rows older than ``RETENTION_DAYS`` are deleted (DPIA risk 6).
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.clock import day_start, now_local, to_local, today_local
from alfred.models import Member, Message

RETENTION_DAYS = 90
LIST_LIMIT = 10
PREVIEW = 45

# Higher wins. "failed" is final unless the message was already delivered/read.
_RANK = {"sent": 1, "delivered": 2, "read": 3}

__all__ = [
    "STRINGS",
    "apply_status",
    "handle_outbox_command",
    "purge_old",
    "record_outbound",
]


# ── Meta status webhook ───────────────────────────────────────────────────────


def _error_text(status: dict) -> str | None:
    errors = status.get("errors") or []
    if not errors or not isinstance(errors[0], dict):
        return None
    e = errors[0]
    code = e.get("code")
    title = e.get("title") or e.get("message") or ""
    return f"{code}: {title}".strip(": ")[:200] or None


async def apply_status(session: AsyncSession, status: dict) -> bool:
    """Apply one ``statuses[]`` entry; True when a stored outbound message was updated."""
    wamid = status.get("id")
    new = status.get("status")
    if not isinstance(wamid, str) or new not in ("sent", "delivered", "read", "failed"):
        return False
    msg = await session.scalar(
        select(Message).where(Message.wa_message_id == wamid, Message.direction == "outbound")
    )
    if msg is None:
        return False
    current = msg.delivery_status
    if new == "failed":
        if current in ("delivered", "read"):
            return False
        msg.delivery_status = "failed"
        msg.delivery_error = _error_text(status)
        return True
    if current == "failed":  # a late "sent" must not hide the failure
        return False
    if _RANK.get(current or "", 0) >= _RANK[new]:
        return False  # out of order or repeated
    msg.delivery_status = new
    msg.delivery_error = None
    return True


# ── proactive sends ───────────────────────────────────────────────────────────


async def record_outbound(
    session: AsyncSession,
    wa_phone: str,
    body: str,
    *,
    kind: str,
    wa_message_id: str | None,
    template_name: str | None = None,
) -> bool:
    """Store a message sent outside a handler run (cron). False when the member is unknown."""
    member = await session.scalar(select(Member).where(Member.wa_phone == wa_phone))
    if member is None:
        return False
    session.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id=wa_message_id or f"out-{uuid.uuid4()}",
            household_id=member.household_id,
            author_id=member.id,
            direction="outbound",
            body=body,
            wa_timestamp=datetime.now(UTC),
            processed=True,
            kind=kind,
            template_name=template_name,
            delivery_status="sent" if wa_message_id else None,
        )
    )
    return True


async def purge_old(session: AsyncSession, now: datetime | None = None) -> int:
    """Delete outbound messages older than the retention period; returns how many."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=RETENTION_DAYS)
    res = await session.execute(
        delete(Message).where(Message.direction == "outbound", Message.created_at < cutoff)
    )
    return res.rowcount or 0


# ── "o que você me enviou" ────────────────────────────────────────────────────

_ASK = re.compile(
    r"^(?:"
    r"(?:o que|que (?:mensagens|lembretes|avisos))\s+(?:voce|vc|tu)\s+(?:me\s+)?(?:enviou|mandou|mandaste|enviaste)|"
    r"(?:quais|que)\s+(?:mensagens|lembretes|avisos)\s+(?:voce|vc)\s+(?:me\s+)?(?:enviou|mandou)|"
    r"(?:lembretes|avisos|mensagens)\s+que\s+(?:voce\s+|vc\s+)?(?:me\s+)?(?:enviou|mandou)|"
    r"what\s+(?:did|have)\s+you\s+(?:send|sent)(?:\s+me)?|"
    r"(?:which\s+)?(?:messages|reminders)\s+(?:did\s+)?you\s+(?:send|sent)(?:\s+me)?|"
    r"wat\s+heb\s+je\s+(?:me\s+)?(?:gestuurd|verstuurd)|"
    r"(?:welke\s+)?(?:berichten|herinneringen)\s+(?:heb\s+je\s+(?:me\s+)?(?:gestuurd|verstuurd)|die\s+je\s+(?:me\s+)?(?:stuurde|hebt\s+gestuurd))|"
    r"qu'?est[- ]ce\s+que\s+tu\s+m'?as\s+envoye|"
    r"(?:rappels|messages)\s+que\s+tu\s+(?:m'?as\s+)?envoye|"
    r"was\s+hast\s+du\s+mir\s+(?:geschickt|gesendet)|"
    r"(?:erinnerungen|nachrichten)\s+die\s+du\s+(?:mir\s+)?(?:geschickt|gesendet)\s+hast"
    r")(?P<rest>.*)$"
)
_TODAY = re.compile(r"\b(?:hoje|today|vandaag|aujourd'?hui|heute)\b")
_REMINDERS = re.compile(r"\b(?:lembretes|reminders|herinneringen|rappels|erinnerungen)\b")


def parse_outbox_query(body_plain: str) -> tuple[bool, bool] | None:
    """``(today_only, reminders_only)`` when the text asks what the bot sent, else None."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    m = _ASK.match(plain)
    if not m:
        return None
    return bool(_TODAY.search(plain)), bool(_REMINDERS.search(plain))


_STATUS_KEY = {
    "sent": "outbox_st_sent",
    "delivered": "outbox_st_delivered",
    "read": "outbox_st_read",
    "failed": "outbox_st_failed",
}


def _preview(body: str | None) -> str:
    # drop WhatsApp formatting marks: cutting at PREVIEW could leave a lone "*" that bolds the rest
    first = next((ln for ln in (body or "").splitlines() if ln.strip()), "")  # first line only
    text = " ".join(first.replace("*", "").replace("_", " ").split())
    return text if len(text) <= PREVIEW else text[: PREVIEW - 1].rstrip() + "…"


async def handle_outbox_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    q = parse_outbox_query(body_plain)
    if q is None:
        return None
    today_only, reminders_only = q
    stmt = select(Message).where(Message.author_id == member.id, Message.direction == "outbound")
    if today_only:
        stmt = stmt.where(Message.created_at >= day_start(today_local()))
    if reminders_only:
        stmt = stmt.where(Message.kind.in_(("reminder", "summary", "alert", "template")))
    rows = (
        (await session.execute(stmt.order_by(Message.created_at.desc()).limit(LIST_LIMIT)))
        .scalars()
        .all()
    )
    if not rows:
        return _t("outbox_empty", lang)
    now = now_local()
    lines = [_t("outbox_title", lang, n=len(rows))]
    for m in rows:
        when = to_local(m.created_at)
        stamp = (
            when.strftime("%H:%M") if when.date() == now.date() else when.strftime("%d/%m %H:%M")
        )
        state = _t(_STATUS_KEY.get(m.delivery_status or "", "outbox_st_unknown"), lang)
        kind = (
            _t(f"outbox_kind_{m.kind}", lang) if m.kind in _KINDS else _t("outbox_kind_reply", lang)
        )
        err = f" ({m.delivery_error})" if m.delivery_status == "failed" and m.delivery_error else ""
        lines.append(f"{stamp} · {kind} · {_preview(m.body)} — {state}{err}")
    return "\n".join(lines)


_KINDS = ("reply", "reminder", "summary", "alert", "template")


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "outbox_title": _all(
        "Últimas {n} mensagens que mandei:",
        "Laatste {n} berichten die ik stuurde:",
        "Last {n} messages I sent:",
        "Les {n} derniers messages que j'ai envoyés :",
        "Die letzten {n} Nachrichten, die ich geschickt habe:",
    ),
    "outbox_empty": _all(
        "Não mandei nada por aqui nesse período.",
        "Ik heb in deze periode niets gestuurd.",
        "I haven't sent anything in that period.",
        "Je n'ai rien envoyé sur cette période.",
        "In diesem Zeitraum habe ich nichts geschickt.",
    ),
    "outbox_kind_reply": _all("resposta", "antwoord", "reply", "réponse", "Antwort"),
    "outbox_kind_reminder": _all("lembrete", "herinnering", "reminder", "rappel", "Erinnerung"),
    "outbox_kind_summary": _all("resumo", "samenvatting", "summary", "résumé", "Zusammenfassung"),
    "outbox_kind_alert": _all("alerta", "melding", "alert", "alerte", "Warnung"),
    "outbox_kind_template": _all("aviso", "bericht", "notice", "avis", "Hinweis"),
    "outbox_st_sent": _all("enviada", "verzonden", "sent", "envoyé", "gesendet"),
    "outbox_st_delivered": _all("entregue", "afgeleverd", "delivered", "remis", "zugestellt"),
    "outbox_st_read": _all("lida", "gelezen", "read", "lu", "gelesen"),
    "outbox_st_failed": _all(
        "não entregue", "niet afgeleverd", "not delivered", "non remis", "nicht zugestellt"
    ),
    "outbox_st_unknown": _all(
        "estado desconhecido",
        "status onbekend",
        "status unknown",
        "statut inconnu",
        "Status unbekannt",
    ),
}
