# ruff: noqa: E501
"""Follow-up questions about bills (hard test F06-28, F04-48, F06-18).

* ``pay_due``      "a pagar internet 45" has no date: when is it due?
* ``pay_due_date`` the date typed after "Outra data"
* ``pay_repeat``   after a bill with a due day: does it repeat every month?
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import questions
from alfred.agenda import parse_when
from alfred.clock import local_tz, month_name
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


# ── F06-18: the same fixed bill with another day ──────────────────────────────

STRINGS.update(
    {
        "day_change_ask": _all(
            "{name} já está no dia {old}. Mudar para o dia {new}?",
            "{name} staat op dag {old}. Verplaatsen naar dag {new}?",
            "{name} is on day {old}. Move it to day {new}?",
            "{name} est au jour {old}. Passer au jour {new} ?",
            "{name} steht auf Tag {old}. Auf Tag {new} ändern?",
        ),
        "btn_day_change": _all("Mudar o dia", "Dag wijzigen", "Change day", "Changer", "Ändern"),
        "btn_day_keep": _all("Manter", "Laten staan", "Keep it", "Garder", "Lassen"),
        "day_change_kept": _all(
            "Certo, mantive o dia {old}.",
            "Prima, dag {old} blijft.",
            "Okay, kept day {old}.",
            "D'accord, je garde le jour {old}.",
            "Okay, Tag {old} bleibt.",
        ),
        "day_paid_ask": _all(
            "Mudei {name} para o dia {new}. A de {month} já foi paga?",
            "{name} staat nu op dag {new}. Is die van {month} al betaald?",
            "{name} now falls on day {new}. Is the {month} one already paid?",
            "{name} tombe maintenant le {new}. Celle de {month} est-elle déjà payée ?",
            "{name} fällt jetzt auf Tag {new}. Ist die von {month} schon bezahlt?",
        ),
        "btn_yes": _all("Sim", "Ja", "Yes", "Oui", "Ja"),
        "btn_no": _all("Não", "Nee", "No", "Non", "Nein"),
        "day_paid_yes": _all(
            "Certo. O próximo vencimento de {name} é {due}.",
            "Prima. De volgende vervaldatum van {name} is {due}.",
            "Okay. The next due date for {name} is {due}.",
            "D'accord. La prochaine échéance de {name} est le {due}.",
            "Okay. Die nächste Fälligkeit von {name} ist {due}.",
        ),
        "day_paid_no": _all(
            "Certo. {name} vence em {due}; eu aviso 3 dias antes.",
            "Prima. {name} vervalt op {due}; ik waarschuw je 3 dagen eerder.",
            "Okay. {name} is due on {due}; I'll remind you 3 days before.",
            "D'accord. {name} est dû le {due} ; je te préviens 3 jours avant.",
            "Okay. {name} ist am {due} fällig; ich erinnere dich 3 Tage vorher.",
        ),
        "income_due_today": _all(
            'Hoje é dia de {name} ({amount}). Já caiu? Se sim, diga "recebi {name}". Até lá fica em "a receber".',
            'Vandaag is het dag van {name} ({amount}). Al binnen? Zeg dan "ontvangen {name}". Tot dan staat het bij "te ontvangen".',
            'Today is {name} day ({amount}). Has it arrived? If so, say "received {name}". Until then it stays under "to receive".',
            "Aujourd'hui, c'est {name} ({amount}). C'est arrivé ? Si oui, dis « reçu {name} ». D'ici là, c'est « à recevoir ».",
            'Heute ist {name} ({amount}) dran. Schon da? Dann sag "erhalten {name}". Bis dahin steht es unter "zu erhalten".',
        ),
    }
)


async def ask_day_change(session: AsyncSession, member: Member, lang: str, item, new_day: int):
    return await questions.ask(
        session,
        member,
        "day_change",
        {"id": str(item.id), "new": new_day, "yes_no": True},
        _t("day_change_ask", lang, name=item.name, old=item.due_day or "?", new=new_day),
        [("yes", _t("btn_day_change", lang)), ("no", _t("btn_day_keep", lang))],
    )


async def _on_day_change(session, member, lang, data, choice, today) -> Reply:
    from alfred import recurring as rec
    from alfred.models import RecurringItem

    item = await session.get(RecurringItem, uuid.UUID(data["id"]))
    if item is None or item.member_id != member.id:
        return Reply(_t("fu_gone", lang), [])
    if choice != "yes":
        return Reply(_t("day_change_kept", lang, old=item.due_day or "?"), [])
    item.due_day = int(data["new"])
    item.next_due_date = rec.first_due(item.frequency, item.due_day, today)
    session.add(item)
    await session.flush()
    return await questions.ask(
        session,
        member,
        "day_paid",
        {"id": data["id"], "yes_no": True},
        _t(
            "day_paid_ask",
            lang,
            name=item.name,
            new=item.due_day,
            month=month_name(item.next_due_date, lang),
        ),
        [("yes", _t("btn_yes", lang)), ("no", _t("btn_no", lang))],
    )


async def _on_day_paid(session, member, lang, data, choice, today) -> Reply:
    from alfred import recurring as rec
    from alfred.models import RecurringItem

    item = await session.get(RecurringItem, uuid.UUID(data["id"]))
    if item is None or item.member_id != member.id:
        return Reply(_t("fu_gone", lang), [])
    key = "day_paid_no"
    if choice == "yes":
        item.next_due_date = rec.advance(item.next_due_date, item.frequency, item.due_day)
        key = "day_paid_yes"
    session.add(item)
    return Reply(_t(key, lang, name=item.name, due=f"{item.next_due_date:%d/%m}"), [])


questions.register("day_change", _on_day_change)
questions.register("day_paid", _on_day_paid)


# ── F06-33: fixed income ("salário 3000 todo dia 25") ─────────────────────────


async def income_reminders(session: AsyncSession, now: datetime) -> list:
    """On the day, a fixed income becomes an "a receber" entry and the member is asked once.

    The due date moves to next month right away, so a second run the same day does nothing.
    """
    import uuid as _uuid

    from alfred import recurring as rec
    from alfred.conversation import _fmt_eur
    from alfred.ledger_status import TO_RECEIVE, BillReminder
    from alfred.models import Expense, Message, RecurringItem

    if not rec.REMINDER_FROM_HOUR <= now.hour < rec.REMINDER_UNTIL_HOUR:
        return []
    today = now.date()
    rows = (
        await session.execute(
            select(RecurringItem, Member.wa_phone, Member.language)
            .join(Member, RecurringItem.member_id == Member.id)
            .where(
                RecurringItem.active.is_(True),
                RecurringItem.kind == "income",
                RecurringItem.next_due_date <= today,
                Member.consent_state == "accepted",
            )
        )
    ).all()
    out = []
    cutoff = now - timedelta(hours=rec.WINDOW_HOURS)
    for item, phone, lang in rows:
        lang = lang or "en"
        due = item.next_due_date
        session.add(
            Expense(
                id=_uuid.uuid4(),
                member_id=item.member_id,
                household_id=item.household_id,
                transaction_type="income",
                amount=item.amount,
                currency="EUR",
                merchant=item.name,
                category="inkomen",
                description=item.name,
                expense_date=datetime.combine(due, time(12, 0), tzinfo=local_tz()),
                status=TO_RECEIVE,
            )
        )
        item.next_due_date = rec.advance(due, item.frequency, item.due_day)
        session.add(item)
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == item.member_id, Message.direction == "inbound"
            )
        )
        out.append(
            BillReminder(
                ref=f"i:{item.id}:{due}",
                member_id=item.member_id,
                wa_phone=phone,
                lang=lang,
                text=_t("income_due_today", lang, name=item.name, amount=_fmt_eur(item.amount)),
                in_window=bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff),
            )
        )
    await session.flush()
    return out
