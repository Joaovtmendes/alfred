# ruff: noqa: E501
"""Follow-up questions about bills (hard test F06-28, F04-48, F06-18).

* ``pay_due``      "a pagar internet 45" has no date: when is it due?
* ``pay_due_date`` the date typed after "Outra data"
* ``pay_repeat``   after a bill with a due day: does it repeat every month?
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from alfred import questions
from alfred.agenda import parse_when
from alfred.clock import local_tz
from alfred.couple import Reply
from alfred.models import Member
from alfred.parsing import strip_accents

_TODAY = {"hoje", "today", "vandaag", "aujourdhui", "heute"}
_TOMORROW = {"amanha", "tomorrow", "morgen", "demain"}


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "pay_due_ask": _all(
        "Anotei {name}, {amount}. Quando vence?",
        "Genoteerd: {name}, {amount}. Wanneer vervalt het?",
        "Noted: {name}, {amount}. When is it due?",
        "Noté : {name}, {amount}. Quelle est l'échéance ?",
        "Notiert: {name}, {amount}. Wann ist es fällig?",
    ),
    "pay_due_date_ask": _all(
        "Qual é a data? Por exemplo: dia 28 ou 15/11.",
        "Welke datum? Bijvoorbeeld: de 28e of 15/11.",
        "Which date? For example: the 28th or 15/11.",
        "Quelle date ? Par exemple : le 28 ou 15/11.",
        "Welches Datum? Zum Beispiel: der 28. oder 15.11.",
    ),
    "btn_due_today": _all("Hoje", "Vandaag", "Today", "Aujourd'hui", "Heute"),
    "btn_due_tomorrow": _all("Amanhã", "Morgen", "Tomorrow", "Demain", "Morgen"),
    "btn_due_other": _all(
        "Outra data", "Andere datum", "Other date", "Autre date", "Anderes Datum"
    ),
    "pay_repeat_ask": _all(
        "Essa conta repete todo mês?",
        "Komt deze rekening elke maand terug?",
        "Does this bill repeat every month?",
        "Cette facture revient-elle chaque mois ?",
        "Wiederholt sich diese Rechnung jeden Monat?",
    ),
    "btn_repeat_yes": _all(
        "Sim, todo mês", "Ja, elke maand", "Yes, monthly", "Oui, chaque mois", "Ja, monatlich"
    ),
    "btn_repeat_no": _all(
        "Só esta vez", "Alleen nu", "Just this once", "Cette fois", "Nur dieses Mal"
    ),
    "pay_repeat_yes": _all(
        "Pronto: {name} agora é uma conta fixa todo mês, dia {day}. O próximo lembrete é em {next}.",
        "Klaar: {name} is nu elke maand een vaste last, dag {day}. De volgende herinnering is op {next}.",
        "Done: {name} is now a monthly fixed bill on day {day}. The next reminder is on {next}.",
        "C'est fait : {name} est maintenant une charge fixe mensuelle, le {day}. Prochain rappel le {next}.",
        "Erledigt: {name} ist jetzt monatlich fix, Tag {day}. Die nächste Erinnerung ist am {next}.",
    ),
    "pay_repeat_no": _all(
        "Certo, só esta vez.",
        "Prima, alleen deze keer.",
        "Okay, just this once.",
        "D'accord, juste cette fois.",
        "Okay, nur dieses Mal.",
    ),
}


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9/ ]", "", strip_accents(text.lower())).strip()


def _day_from(text: str) -> date | None:
    now = datetime.combine(date.today(), time(0, 0), tzinfo=local_tz())
    return parse_when(_plain(text), now).day


# ── pay_due: "a pagar internet 45" ────────────────────────────────────────────


async def ask_due(session: AsyncSession, member: Member, lang: str, new) -> Reply:
    from alfred.conversation import _fmt_eur

    text = _t("pay_due_ask", lang, name=new.name, amount=_fmt_eur(new.amount))
    return await questions.ask(
        session,
        member,
        "pay_due",
        {"name": new.name, "amount": new.amount},
        text,
        [
            ("today", _t("btn_due_today", lang)),
            ("tomorrow", _t("btn_due_tomorrow", lang)),
            ("other", _t("btn_due_other", lang)),
        ],
    )


def _parse_due(text: str, data: dict) -> str | None:
    word = _plain(text).replace(" ", "")
    if word in _TODAY:
        return "today"
    if word in _TOMORROW:
        return "tomorrow"
    day = _day_from(text)
    return f"d:{day.isoformat()}" if day else None


def _parse_date_only(text: str, data: dict) -> str | None:
    day = _day_from(text)
    return f"d:{day.isoformat()}" if day else None


async def _on_due(session, member, lang, data, choice, today) -> Reply:
    if choice == "other":
        return await questions.ask(
            session, member, "pay_due_date", data, _t("pay_due_date_ask", lang), []
        )
    if choice == "today":
        due = today
    elif choice == "tomorrow":
        due = today + timedelta(days=1)
    else:
        due = date.fromisoformat(choice[2:])
    return await create_with_repeat(session, member, lang, data["name"], float(data["amount"]), due)


async def create_with_repeat(
    session: AsyncSession, member: Member, lang: str, name: str, amount: float, due: date
) -> Reply:
    """Save the bill as to-pay, then ask whether it repeats every month."""
    from alfred.ledger_status import TO_PAY, NewPending, _create

    text = await _create(NewPending(TO_PAY, name, amount, due), member, lang, session)
    return await with_repeat_question(session, member, lang, name, amount, due, text)


async def with_repeat_question(
    session: AsyncSession, member: Member, lang: str, name: str, amount: float, due: date, text: str
) -> Reply:
    return await questions.ask(
        session,
        member,
        "pay_repeat",
        {"name": name, "amount": amount, "day": due.day, "due": due.isoformat(), "yes_no": True},
        f"{text}\n\n{_t('pay_repeat_ask', lang)}",
        [("yes", _t("btn_repeat_yes", lang)), ("no", _t("btn_repeat_no", lang))],
    )


async def _on_repeat(session, member, lang, data, choice, today) -> Reply:
    if choice != "yes":
        return Reply(_t("pay_repeat_no", lang), [])
    from alfred import recurring as rec

    due = date.fromisoformat(data["due"])
    parsed = rec.Parsed(
        name=data["name"],
        amount=float(data["amount"]),
        frequency="monthly",
        due_day=int(data["day"]),
        installments_total=None,
        category=rec.guess_category(_plain(data["name"])),
        kind="fixed",
    )
    # this month's bill is already tracked as to-pay: the fixed bill starts next month
    out = await rec._create(
        parsed, member, lang, session, first=rec.advance(due, "monthly", due.day)
    )
    if isinstance(out, Reply):
        return out
    next_due = rec.advance(due, "monthly", due.day)
    return Reply(
        _t("pay_repeat_yes", lang, name=data["name"], day=data["day"], next=f"{next_due:%d/%m}"), []
    )


questions.register("pay_due", _on_due, _parse_due)
questions.register("pay_due_date", _on_due, _parse_date_only)
questions.register("pay_repeat", _on_repeat)
