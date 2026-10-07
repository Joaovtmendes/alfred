# ruff: noqa: E501
"""V2-20 buttons to go deeper after a spending answer, and V2-23 thumbs up/down feedback.

Deeper buttons: after "quanto gastei em mercado este mês" the member can tap [Por categoria]
[Maiores gastos] [Ajustar orçamento]. The button id carries only the period (dates) and the
category key, so the tap rebuilds the answer from the member's own rows; nothing is stored.

Feedback: after an analysis or a long model answer the member can tap 👍 / 👎. We keep one row of
counts (source, rating, and for 👎 an optional reason from a closed list). Never the reply text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import day_start, month_name, month_start, to_local, today_local
from alfred.labels import category_label
from alfred.models import SETTLED, Budget, Expense, Member, ReplyFeedback
from alfred.training import Reply
from alfred.validation import CATEGORIES

TOP_N = 5
FEEDBACK_PER_DAY = 20
SOURCES = ("analysis", "chat")
REASONS = ("wrong", "unclear", "missing")
MIN_CHAT_LEN = 200  # shorter model answers are small talk: no feedback buttons


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


# ── V2-20 — go deeper ─────────────────────────────────────────────────────────


def _enc(start: datetime, end_excl: datetime | None, category: str | None) -> str:
    s = to_local(start).date().isoformat()
    e = to_local(end_excl).date().isoformat() if end_excl else "open"
    return f"{s}.{e}.{category or '-'}"


def _dec(raw: str) -> tuple[datetime, datetime | None, str | None] | None:
    """Back from a button id; None when it is not a well-formed scope (a forged id)."""
    parts = raw.split(".")
    if len(parts) != 3:
        return None
    try:
        start = day_start(date.fromisoformat(parts[0]))
        end = None if parts[1] == "open" else day_start(date.fromisoformat(parts[1]))
    except ValueError:
        return None
    category = parts[2] if parts[2] in CATEGORIES else None
    if parts[2] not in ("-", *CATEGORIES):
        return None
    if end is not None and end <= start:
        return None
    return start, end, category


def _label(start: datetime, end_excl: datetime | None, lang: str) -> str:
    first = to_local(start).date()
    last = to_local(end_excl).date() - timedelta(days=1) if end_excl else today_local()
    if (
        first.day == 1
        and end_excl is not None
        and month_start(first + timedelta(days=32)) == (last + timedelta(days=1))
    ):
        return month_name(first, lang, year=True)
    if end_excl is None and first.day == 1 and first.month == today_local().month:
        return month_name(first, lang, year=True)
    return f"{first:%d/%m} – {last:%d/%m}"


def deeper_buttons(
    start: datetime, end_excl: datetime | None, category: str | None, lang: str
) -> list[tuple[str, str]]:
    """The buttons after a spending answer (``category`` set → also "Por categoria")."""
    scope = _enc(start, end_excl, category)
    out: list[tuple[str, str]] = []
    if category:
        out.append((f"dd_cat:{scope}", _t("btn_dd_cat", lang)))
    out.append((f"dd_top:{scope}", _t("btn_dd_top", lang)))
    out.append((f"dd_bud:{scope}", _t("btn_dd_bud", lang)))
    return out


async def _top(
    member: Member,
    start: datetime,
    end_excl: datetime | None,
    category: str | None,
    lang: str,
    session: AsyncSession,
) -> str:
    filters = [
        Expense.member_id == member.id,
        Expense.transaction_type == "expense",
        Expense.status.in_(SETTLED),
        Expense.expense_date >= start,
    ]
    if end_excl:
        filters.append(Expense.expense_date < end_excl)
    if category:
        filters.append(Expense.category == category)
    rows = (
        (
            await session.execute(
                select(Expense).where(*filters).order_by(Expense.amount.desc()).limit(TOP_N)
            )
        )
        .scalars()
        .all()
    )
    label = _label(start, end_excl, lang)
    if not rows:
        return _t("dd_top_none", lang, period_label=label)
    from alfred.conversation import _fmt_eur

    if category:
        title = _t(
            "dd_top_title_cat", lang, category=category_label(category, lang), period_label=label
        )
    else:
        title = _t("dd_top_title", lang, period_label=label)
    lines = [title + "\n"]
    for e in rows:
        name = e.merchant or e.description or category_label(e.category or "overig", lang)
        lines.append(f"• {to_local(e.expense_date):%d/%m} {name}: {_fmt_eur(e.amount)}")
    return "\n".join(lines)


async def _budget(member: Member, category: str | None, lang: str, session: AsyncSession) -> str:
    from alfred.budgets import _dec as to_dec
    from alfred.budgets import month_spent, percent
    from alfred.conversation import _fmt_eur

    if category is None:
        return _t("dd_bud_hint", lang)
    cat = category_label(category, lang)
    row = await session.scalar(
        select(Budget).where(Budget.member_id == member.id, Budget.category == category)
    )
    if row is None:
        return _t("dd_bud_none", lang, cat=cat)
    spent = await month_spent(session, member.id, category, today_local())
    limit = to_dec(row.monthly_limit)
    return _t(
        "dd_bud_has",
        lang,
        cat=cat,
        spent=_fmt_eur(spent),
        limit=_fmt_eur(limit),
        pct=percent(spent, limit),
    )


async def handle_deeper_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> str:
    """Reply for ``dd_cat`` / ``dd_top`` / ``dd_bud``; a malformed id gets a neutral answer."""
    scope = _dec(raw_id)
    if scope is None:
        return _t("button_gone", lang)
    start, end_excl, category = scope
    if action == "dd_top":
        return await _top(member, start, end_excl, category, lang, session)
    if action == "dd_bud":
        return await _budget(member, category, lang, session)
    from alfred.conversation import _build_summary

    return await _build_summary(
        member, session, bounds=(start, end_excl, _label(start, end_excl, lang))
    )


# ── V2-23 — thumbs up / down ──────────────────────────────────────────────────


def feedback_buttons(source: str, lang: str) -> list[tuple[str, str]]:
    return [
        (f"fb_up:{source}", _t("btn_fb_up", lang)),
        (f"fb_down:{source}", _t("btn_fb_down", lang)),
    ]


async def handle_feedback_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> Reply:
    """Record a 👍/👎 (or the reason after a 👎); nothing else about the reply is kept."""
    if action == "fb_r":
        row_id, _, reason = raw_id.partition(":")
        try:
            fid = uuid.UUID(row_id)
        except ValueError:
            return Reply(_t("button_gone", lang))
        row = await session.scalar(
            select(ReplyFeedback).where(
                ReplyFeedback.id == fid, ReplyFeedback.member_id == member.id
            )
        )
        if row is None or row.rating != "down" or reason not in REASONS:
            return Reply(_t("button_gone", lang))
        row.reason = reason
        session.add(row)
        return Reply(_t("fb_thanks", lang))

    source = raw_id if raw_id in SOURCES else "chat"
    since = datetime.now(UTC) - timedelta(days=1)
    recent = await session.scalar(
        select(func.count())
        .select_from(ReplyFeedback)
        .where(ReplyFeedback.member_id == member.id, ReplyFeedback.created_at >= since)
    )
    if (recent or 0) >= FEEDBACK_PER_DAY:
        return Reply(_t("fb_thanks", lang))
    row = ReplyFeedback(
        id=uuid.uuid4(),
        member_id=member.id,
        source=source,
        rating="up" if action == "fb_up" else "down",
    )
    session.add(row)
    await session.flush()
    audit(session, "reply_feedback", member.id)
    if action == "fb_up":
        return Reply(_t("fb_thanks", lang))
    return Reply(
        _t("fb_ask_reason", lang),
        [(f"fb_r:{row.id}:{r}", _t(f"btn_fb_{r}", lang)) for r in REASONS],
    )


async def counts(session: AsyncSession, since: datetime) -> dict:
    """Totals for the internal metrics: up, down, and 👎 by reason."""
    rows = (
        await session.execute(
            select(ReplyFeedback.rating, ReplyFeedback.reason, func.count())
            .where(ReplyFeedback.created_at >= since)
            .group_by(ReplyFeedback.rating, ReplyFeedback.reason)
        )
    ).all()
    out: dict = {"up": 0, "down": 0, "reasons": {}}
    for rating, reason, n in rows:
        out[rating] += int(n)
        if reason:
            out["reasons"][reason] = out["reasons"].get(reason, 0) + int(n)
    return out


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "btn_dd_cat": _all(
        "Por categoria", "Per categorie", "By category", "Par catégorie", "Nach Kategorie"
    ),
    "btn_dd_top": _all(
        "Maiores gastos",
        "Grootste uitgaven",
        "Biggest expenses",
        "Grosses dépenses",
        "Größte Ausgaben",
    ),
    "btn_dd_bud": _all(
        "Ajustar orçamento",
        "Budget aanpassen",
        "Adjust budget",
        "Ajuster budget",
        "Budget anpassen",
    ),
    "dd_top_title": _all(
        "Maiores gastos — {period_label}:",
        "Grootste uitgaven — {period_label}:",
        "Biggest expenses — {period_label}:",
        "Plus grosses dépenses — {period_label} :",
        "Größte Ausgaben — {period_label}:",
    ),
    "dd_top_title_cat": _all(
        "Maiores gastos em {category} — {period_label}:",
        "Grootste uitgaven in {category} — {period_label}:",
        "Biggest expenses in {category} — {period_label}:",
        "Plus grosses dépenses en {category} — {period_label} :",
        "Größte Ausgaben bei {category} — {period_label}:",
    ),
    "dd_top_none": _all(
        "Não encontrei gastos em {period_label}.",
        "Ik vond geen uitgaven in {period_label}.",
        "I found no expenses in {period_label}.",
        "Je n'ai trouvé aucune dépense pour {period_label}.",
        "Ich habe keine Ausgaben für {period_label} gefunden.",
    ),
    "dd_bud_has": _all(
        "Orçamento de *{cat}*: {spent} de {limit} ({pct}%) neste mês. Para mudar, escreva por exemplo *orçamento {cat} 450*.",
        "Budget voor *{cat}*: {spent} van {limit} ({pct}%) deze maand. Wijzigen kan met bijvoorbeeld *budget {cat} 450*.",
        "Budget for *{cat}*: {spent} of {limit} ({pct}%) this month. To change it, write for example *budget {cat} 450*.",
        "Budget pour *{cat}* : {spent} sur {limit} ({pct} %) ce mois-ci. Pour le changer, écris par exemple *budget {cat} 450*.",
        "Budget für *{cat}*: {spent} von {limit} ({pct} %) in diesem Monat. Zum Ändern schreib zum Beispiel *budget {cat} 450*.",
    ),
    "dd_bud_none": _all(
        "Você ainda não tem orçamento para *{cat}*. Para criar, escreva por exemplo *orçamento {cat} 400*.",
        "Je hebt nog geen budget voor *{cat}*. Maak er een met bijvoorbeeld *budget {cat} 400*.",
        "You don't have a budget for *{cat}* yet. To create one, write for example *budget {cat} 400*.",
        "Tu n'as pas encore de budget pour *{cat}*. Pour en créer un, écris par exemple *budget {cat} 400*.",
        "Du hast noch kein Budget für *{cat}*. Zum Anlegen schreib zum Beispiel *budget {cat} 400*.",
    ),
    "dd_bud_hint": _all(
        "Para criar ou mudar um orçamento mensal, escreva por exemplo *orçamento mercado 400*. Para ver os seus, escreva *meus orçamentos*.",
        "Om een maandbudget te maken of te wijzigen, schrijf bijvoorbeeld *budget supermarkt 400*. Je ziet ze met *mijn budgetten*.",
        "To create or change a monthly budget, write for example *budget groceries 400*. To see yours, write *my budgets*.",
        "Pour créer ou changer un budget mensuel, écris par exemple *budget courses 400*. Pour voir les tiens, écris *mes budgets*.",
        "Um ein Monatsbudget anzulegen oder zu ändern, schreib zum Beispiel *budget lebensmittel 400*. Deine siehst du mit *meine budgets*.",
    ),
    "btn_fb_up": _all("👍 Útil", "👍 Handig", "👍 Helpful", "👍 Utile", "👍 Hilfreich"),
    "btn_fb_down": _all(
        "👎 Não ajudou", "👎 Niet handig", "👎 Not helpful", "👎 Pas utile", "👎 Nicht hilfreich"
    ),
    "btn_fb_wrong": _all("Errado", "Onjuist", "Wrong", "Incorrect", "Falsch"),
    "btn_fb_unclear": _all("Não entendi", "Onduidelijk", "Unclear", "Pas clair", "Unklar"),
    "btn_fb_missing": _all(
        "Faltou algo", "Iets ontbreekt", "Missing info", "Il manque qqch", "Etwas fehlt"
    ),
    "fb_thanks": _all(
        "Obrigado, isso me ajuda a melhorar.",
        "Bedankt, dat helpt me beter te worden.",
        "Thanks, that helps me improve.",
        "Merci, ça m'aide à m'améliorer.",
        "Danke, das hilft mir, besser zu werden.",
    ),
    "fb_ask_reason": _all(
        "Sinto muito. O que não funcionou? Se preferir, ignore esta pergunta.",
        "Jammer. Wat werkte er niet? Je mag deze vraag ook negeren.",
        "Sorry about that. What didn't work? You can also ignore this question.",
        "Désolé. Qu'est-ce qui n'a pas marché ? Tu peux aussi ignorer cette question.",
        "Das tut mir leid. Was hat nicht funktioniert? Du kannst die Frage auch ignorieren.",
    ),
}
