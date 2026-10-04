# ruff: noqa: E501
"""V2-17 — confirm a message with 2+ entries before anything is written.

"café 3,50 e padaria 8" becomes a draft with [Confirmar] [Ajustar] [Desfazer]. Until the
member confirms, nothing is in the ledger, so no text of the draft may say it was saved
(see ``conversation._claims_recorded``). A single expense still goes straight in with [Desfazer].

One live draft per member (a newer one replaces the older), valid for 15 minutes. The draft row
is deleted atomically on confirm/cancel, so a repeated tap finds nothing and writes nothing twice.
Ids on the buttons: ``batch_ok:<id>`` / ``batch_edit:<id>`` / ``batch_cancel:<id>``.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import now_local
from alfred.models import Expense, Member, PendingBatch, Trip
from alfred.parsing import to_amount
from alfred.validation import MAX_AMOUNT

TTL = timedelta(minutes=15)
MAX_ITEMS = 10

__all__ = [
    "STRINGS",
    "BatchReply",
    "create_draft",
    "handle_batch_button",
    "handle_batch_text",
    "pending_batch",
]


@dataclass
class BatchReply:
    text: str
    buttons: list[tuple[str, str]] = field(default_factory=list)


# ── items ─────────────────────────────────────────────────────────────────────


def _valid(item: dict) -> bool:
    try:
        amount = float(item["amount"])
    except (KeyError, TypeError, ValueError):
        return False
    return (
        math.isfinite(amount) and 0 < amount <= MAX_AMOUNT and item.get("currency", "EUR") == "EUR"
    )


def _store(item: dict) -> dict:
    return {
        "type": "income" if item.get("type") == "income" else "expense",
        "amount": round(float(item["amount"]), 2),
        "merchant": item.get("merchant"),
        "category": item.get("category") or "overig",
        "description": item.get("description"),
        "days_ago": int(item.get("days_ago") or 0),
    }


def _name(item: dict) -> str:
    return item.get("merchant") or item.get("description") or item.get("category") or "?"


def _lines(items: list[dict]) -> str:
    from alfred.conversation import _fmt_eur

    out = []
    for i, it in enumerate(items, 1):
        sign = "+" if it["type"] == "income" else ""
        out.append(f"{i}. {_name(it)}: {sign}{_fmt_eur(it['amount'])}")
    return "\n".join(out)


def _buttons(batch_id: uuid.UUID, lang: str) -> list[tuple[str, str]]:
    from alfred.conversation import _t

    return [
        (f"batch_ok:{batch_id}", _t("batch_btn_ok", lang)),
        (f"batch_edit:{batch_id}", _t("batch_btn_edit", lang)),
        (f"batch_cancel:{batch_id}", _t("batch_btn_cancel", lang)),
    ]


def _draft_text(items: list[dict], lang: str) -> str:
    from alfred.conversation import _t

    return f"{_t('batch_title', lang, n=len(items))}\n{_lines(items)}\n\n{_t('batch_ask', lang)}"


# ── storage ───────────────────────────────────────────────────────────────────


async def pending_batch(session: AsyncSession, member_id: uuid.UUID) -> PendingBatch | None:
    return await session.scalar(
        select(PendingBatch)
        .where(PendingBatch.member_id == member_id)
        .order_by(PendingBatch.created_at.desc())
        .limit(1)
    )


def clean_items(items: list[dict]) -> list[dict]:
    """Only the valid euro entries (amount > 0 and within the cap), in storable form."""
    return [_store(i) for i in items if _valid(i)][:MAX_ITEMS]


async def create_draft(
    session: AsyncSession, member: Member, items: list[dict], lang: str
) -> BatchReply | None:
    """Store a draft and return the message to show; None when fewer than 2 usable items remain."""
    clean = clean_items(items)
    if len(clean) < 2:
        return None
    await session.execute(delete(PendingBatch).where(PendingBatch.member_id == member.id))
    batch = PendingBatch(
        id=uuid.uuid4(), member_id=member.id, items=clean, expires_at=now_local() + TTL
    )
    session.add(batch)
    await session.flush()
    return BatchReply(_draft_text(clean, lang), _buttons(batch.id, lang))


async def _take(session: AsyncSession, member_id: uuid.UUID, batch_id: uuid.UUID | None):
    """Atomically delete the draft and return ``(items, expired)``; None when it does not exist."""
    stmt = delete(PendingBatch).where(PendingBatch.member_id == member_id)
    if batch_id is not None:
        stmt = stmt.where(PendingBatch.id == batch_id)
    row = (
        await session.execute(stmt.returning(PendingBatch.items, PendingBatch.expires_at))
    ).first()
    if row is None:
        return None
    return list(row.items), row.expires_at < now_local()


MAX_UNDO_IDS = 5  # a reply-button id holds at most 256 characters


async def record_items(
    session: AsyncSession,
    member: Member,
    items: list[dict],
    lang: str,
    ids_out: list[uuid.UUID] | None = None,
) -> str:
    from alfred.budgets import alerts_for_categories
    from alfred.conversation import _fmt_eur, _t
    from alfred.settings import settings

    trip = await session.scalar(
        select(Trip).where(Trip.member_id == member.id, Trip.active.is_(True))
    )
    any_high = False
    for it in items:
        new_id = uuid.uuid4()
        if ids_out is not None:
            ids_out.append(new_id)
        session.add(
            Expense(
                id=new_id,
                member_id=member.id,
                household_id=member.household_id,
                transaction_type=it["type"],
                amount=it["amount"],
                currency="EUR",
                merchant=it["merchant"],
                category=it["category"],
                description=it["description"],
                expense_date=now_local() - timedelta(days=it["days_ago"]),
                trip_id=trip.id if trip else None,
            )
        )
        if it["amount"] >= settings.high_value_threshold and it["type"] != "income":
            any_high = True
    await session.flush()
    audit(session, "batch_confirmed", member.id)
    lines = [
        f"• {_name(it)}: {'+' if it['type'] == 'income' else ''}{_fmt_eur(it['amount'])}"
        for it in items
    ]
    reply = "\n".join([_t("multi_recorded_title", lang, n=len(items)), *lines])
    reply += await alerts_for_categories(
        session, member, [i["category"] for i in items if i["type"] == "expense"], lang
    )
    if any_high:
        reply += _t("high_value_hint", lang)
    return reply


async def _recorded(
    session: AsyncSession, member: Member, items: list[dict], lang: str
) -> BatchReply:
    """The confirmation, with [Undo all] when the ids fit in a button."""
    ids: list[uuid.UUID] = []
    text = await record_items(session, member, items, lang, ids)
    if 0 < len(ids) <= MAX_UNDO_IDS:
        from alfred.conversation import _t

        payload = ",".join(str(i) for i in ids)
        return BatchReply(text, [(f"batch_undo:{payload}", _t("batch_btn_undo_all", lang))])
    return BatchReply(text)


async def undo_recorded(raw_ids: str, member: Member, lang: str, session: AsyncSession) -> str:
    """Delete the entries of a just-confirmed batch (only this member's; a second tap finds none)."""
    from alfred.conversation import _t

    ids: list[uuid.UUID] = []
    for part in raw_ids.split(",")[:MAX_UNDO_IDS]:
        try:
            ids.append(uuid.UUID(part))
        except ValueError:
            continue
    if not ids:
        return _t("batch_gone", lang)
    rows = (
        (
            await session.execute(
                select(Expense).where(Expense.member_id == member.id, Expense.id.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await session.delete(row)
    await session.flush()
    audit(session, "batch_undone", member.id, n=len(rows))
    return _t("batch_undone", lang, n=len(rows)) if rows else _t("batch_gone", lang)


# ── buttons ───────────────────────────────────────────────────────────────────


async def handle_batch_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> BatchReply:
    from alfred.conversation import _t

    try:
        batch_id = uuid.UUID(raw_id)
    except ValueError:
        return BatchReply(_t("batch_gone", lang))
    if action == "batch_edit":
        batch = await session.scalar(
            select(PendingBatch).where(
                PendingBatch.id == batch_id, PendingBatch.member_id == member.id
            )
        )
        if batch is None:
            return BatchReply(_t("batch_gone", lang))
        return BatchReply(_t("batch_edit_hint", lang), _buttons(batch.id, lang))
    taken = await _take(session, member.id, batch_id)
    if taken is None:
        return BatchReply(_t("batch_gone", lang))
    items, expired = taken
    if expired:
        return BatchReply(_t("batch_expired", lang))
    if action == "batch_cancel":
        return BatchReply(_t("batch_cancelled", lang))
    return await _recorded(session, member, items, lang)


# ── text ──────────────────────────────────────────────────────────────────────

_YES = {
    "sim", "ok", "okay", "okey", "confirmo", "confirma", "confirmar", "pode", "isso", "certo",
    "yes", "yep", "yeah", "confirm", "ja", "klopt", "oke", "prima", "oui", "d'accord", "dacord",
    "confirme", "stimmt", "genau", "bestatige", "passt",
}  # fmt: skip
_NO = {
    "nao", "cancela", "cancelar", "cancele", "deixa", "esquece", "no", "nope", "cancel", "nee",
    "annuleer", "annuleren", "non", "annule", "annuler", "nein", "abbrechen", "storno",
}  # fmt: skip

_ORD = {
    "primeiro": 1, "primeira": 1, "first": 1, "eerste": 1, "premier": 1, "premiere": 1, "erste": 1, "ersten": 1, "erster": 1,
    "segundo": 2, "segunda": 2, "second": 2, "tweede": 2, "deuxieme": 2, "zweite": 2, "zweiten": 2, "zweiter": 2,
    "terceiro": 3, "terceira": 3, "third": 3, "derde": 3, "troisieme": 3, "dritte": 3, "dritten": 3, "dritter": 3,
    "quarto": 4, "quarta": 4, "fourth": 4, "vierde": 4, "quatrieme": 4, "vierte": 4, "vierten": 4, "vierter": 4,
    "quinto": 5, "quinta": 5, "fifth": 5, "vijfde": 5, "cinquieme": 5, "funfte": 5, "funften": 5, "funfter": 5,
    "ultimo": -1, "ultima": -1, "last": -1, "laatste": -1, "dernier": -1, "derniere": -1, "letzte": -1, "letzten": -1, "letzter": -1,
}  # fmt: skip
_ORD_RE = (
    r"(?:"
    + "|".join(sorted(_ORD, key=len, reverse=True))
    + r"|(?:item|nr|nummer|numero|no|n)?\s*(\d{1,2})\s*(?:o|º|°|e|de|ste|nd|st|rd|th)?)"
)
_REMOVE_WORDS = (
    r"(?:tira|tire|tirar|remove|remova|remover|apaga|apague|apagar|exclui|delete|drop|take out|get rid of|"
    r"haal|verwijder|schrap|enleve|enlever|retire|retirer|supprime|supprimer|entferne|loesche|losche|streich)"
)
_REMOVE = re.compile(
    rf"^{_REMOVE_WORDS}\s+(?:o |a |os |as |the |de |het |le |la |l'|den |die |das |item )?(?P<ord>.+?)(?:\s+(?:item|lancamento|uitgave|entree|eintrag|weg|out))?$"
)
_CHANGE = re.compile(
    rf"^(?:o |a |the |de |het |le |la |der |die |das )?(?P<ord>{_ORD_RE})\s+(?:item\s+)?(?:foi|era|e|é|was|were|is|etait|est|war|ist|waren|sont)?\s*(?:de\s+)?(?:€\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|eur|euros?)?$"
)  # fmt: skip


def _ordinal(token: str, count: int) -> int | None:
    """1-based index named by ``token`` ("segundo", "2", "2o", "last"), or None."""
    token = token.strip().strip(".")
    if token in _ORD:
        n = _ORD[token]
        return count if n == -1 else n
    m = re.fullmatch(
        r"(?:item |nr |nummer |numero |no |n )?(\d{1,2})\s*(?:o|º|°|e|de|ste|nd|st|rd|th)?", token
    )
    return int(m.group(1)) if m else None


async def handle_batch_text(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> BatchReply | None:
    """Text answers to a live draft: yes / no / "tira o segundo" / "o primeiro foi 4".

    Returns None when there is no draft or the text is about something else, so a bare "sim"
    without a pending draft still reaches the old "there is nothing to confirm" answer.
    """
    from alfred.conversation import _t

    plain = " ".join(body_plain.split()).strip(" .!?,")
    words = set(plain.replace(",", " ").split())
    is_yes = plain in _YES or (len(words) <= 3 and bool(words & _YES) and not words & _NO)
    is_no = plain in _NO or (len(words) <= 3 and bool(words & _NO) and not words & _YES)
    rm = _REMOVE.match(plain)
    ch = _CHANGE.match(plain)
    if not (is_yes or is_no or rm or ch):
        return None  # cheap exit: most messages never touch the database here
    batch = await pending_batch(session, member.id)
    if batch is None:
        return None
    items = list(batch.items)

    if batch.expires_at < now_local():
        await session.execute(delete(PendingBatch).where(PendingBatch.id == batch.id))
        return BatchReply(_t("batch_expired", lang))

    if is_yes or is_no:
        taken = await _take(session, member.id, batch.id)
        if taken is None:
            return BatchReply(_t("batch_gone", lang))
        items, expired = taken
        if expired:
            return BatchReply(_t("batch_expired", lang))
        if is_no:
            return BatchReply(_t("batch_cancelled", lang))
        return await _recorded(session, member, items, lang)

    if rm:
        idx = _ordinal(rm.group("ord"), len(items))
        if idx is None or not 1 <= idx <= len(items):
            return None  # "tira ..." about something else
        del items[idx - 1]
        if not items:
            await session.execute(delete(PendingBatch).where(PendingBatch.id == batch.id))
            return BatchReply(_t("batch_empty", lang))
    else:
        assert ch is not None
        idx = _ordinal(ch.group("ord"), len(items))
        if idx is None or not 1 <= idx <= len(items):
            return BatchReply(_t("batch_no_item", lang, n=ch.group("ord")))
        amount = to_amount(ch.group("amt"))
        if amount is None or not 0 < float(amount) <= MAX_AMOUNT:
            return BatchReply(_t("batch_bad_amount", lang))
        items[idx - 1] = {**items[idx - 1], "amount": round(float(amount), 2)}

    batch.items = items  # reassign: JSONB change tracking needs a new object
    batch.expires_at = now_local() + TTL
    await session.flush()
    return BatchReply(_draft_text(items, lang), _buttons(batch.id, lang))


# ── texts ─────────────────────────────────────────────────────────────────────
# No sentence below may claim anything was saved: nothing is, until the member confirms.


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "batch_title": _all(
        "Entendi {n} lançamentos:",
        "Ik zie {n} transacties:",
        "I see {n} entries:",
        "Je vois {n} opérations :",
        "Ich sehe {n} Buchungen:",
    ),
    "batch_ask": _all(
        "Pode confirmar para eu gravar? Ainda não gravei nada.",
        "Bevestig je ze? Pas daarna komen ze in je overzicht.",
        "Confirm and I'll add them. Nothing has been added yet.",
        "Tu confirmes ? Rien n'est encore ajouté.",
        "Soll ich sie übernehmen? Bisher steht nichts in deiner Übersicht.",
    ),
    "batch_btn_ok": _all("Confirmar", "Bevestigen", "Confirm", "Confirmer", "Bestätigen"),
    "batch_btn_edit": _all("Ajustar", "Aanpassen", "Adjust", "Ajuster", "Anpassen"),
    "batch_btn_undo_all": _all(
        "Desfazer tudo", "Alles ongedaan", "Undo all", "Tout annuler", "Alles rückgängig"
    ),
    "batch_undone": _all(
        "Desfeito: {n} lançamentos apagados.",
        "Ongedaan gemaakt: {n} boekingen verwijderd.",
        "Undone: {n} entries removed.",
        "Annulé : {n} écritures supprimées.",
        "Rückgängig: {n} Buchungen gelöscht.",
    ),
    "batch_btn_cancel": _all("Desfazer", "Ongedaan maken", "Undo", "Annuler", "Rückgängig"),
    "batch_edit_hint": _all(
        'Diga o que mudar: "tira o segundo" ou "o primeiro foi 4". Depois confirme.',
        'Zeg wat er moet veranderen: "haal de tweede weg" of "de eerste was 4". Bevestig daarna.',
        'Tell me what to change: "remove the second" or "the first was 4". Then confirm.',
        "Dis-moi quoi changer : « enlève le deuxième » ou « le premier était 4 ». Puis confirme.",
        'Sag, was sich ändern soll: "entferne den zweiten" oder "der erste war 4". Dann bestätige.',
    ),
    "batch_gone": _all(
        "Não há nada pendente para confirmar. Envie os lançamentos novamente, se precisar.",
        "Er staat niets meer klaar om te bevestigen. Stuur de transacties opnieuw als dat nodig is.",
        "There is nothing waiting for confirmation. Send the entries again if you need to.",
        "Rien n'attend de confirmation. Renvoie les opérations si besoin.",
        "Es wartet nichts auf Bestätigung. Sende die Buchungen bei Bedarf erneut.",
    ),
    "batch_expired": _all(
        "Essa lista expirou (passaram 15 minutos) e eu descartei. Envie novamente e eu monto outra.",
        "Die lijst is verlopen (15 minuten) en weggegooid. Stuur ze opnieuw, dan maak ik een nieuwe.",
        "That list expired (15 minutes) and I dropped it. Send it again and I'll build a new one.",
        "Cette liste a expiré (15 minutes) et je l'ai écartée. Renvoie-la et j'en refais une.",
        "Diese Liste ist abgelaufen (15 Minuten) und verworfen. Sende sie erneut, dann erstelle ich eine neue.",
    ),
    "batch_cancelled": _all(
        "Cancelado. Não gravei nada.",
        "Geannuleerd. Er is niets toegevoegd.",
        "Cancelled. Nothing was added.",
        "Annulé. Rien n'a été ajouté.",
        "Abgebrochen. Es wurde nichts hinzugefügt.",
    ),
    "batch_empty": _all(
        "Tirei tudo, então cancelei a lista. Não gravei nada.",
        "Alles verwijderd, dus de lijst is geannuleerd. Er is niets toegevoegd.",
        "I removed everything, so the list is cancelled. Nothing was added.",
        "J'ai tout retiré, la liste est donc annulée. Rien n'a été ajouté.",
        "Alles entfernt, die Liste ist abgebrochen. Es wurde nichts hinzugefügt.",
    ),
    "batch_no_item": _all(
        'Não achei o item "{n}" na lista. Use o número da linha.',
        'Ik vind item "{n}" niet in de lijst. Gebruik het regelnummer.',
        'I can\'t find item "{n}" in the list. Use the line number.',
        "Je ne trouve pas l'élément « {n} » dans la liste. Utilise le numéro de ligne.",
        'Ich finde Eintrag "{n}" nicht in der Liste. Nutze die Zeilennummer.',
    ),
    "batch_bad_amount": _all(
        "Esse valor não serve (zero, negativo ou grande demais). A lista continua como estava.",
        "Dat bedrag kan niet (nul, negatief of te groot). De lijst blijft zoals ze was.",
        "That amount won't work (zero, negative or too large). The list stays as it was.",
        "Ce montant ne convient pas (zéro, négatif ou trop grand). La liste reste telle quelle.",
        "Dieser Betrag geht nicht (null, negativ oder zu groß). Die Liste bleibt wie sie war.",
    ),
}
