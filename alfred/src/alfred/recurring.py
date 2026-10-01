"""V2-02 — contas fixas: rent, subscriptions and instalment plans with due-date reminders.

Rules and SQL only (no LLM). The router calls ``handle_recurring_command`` before the expense
extractor; the cron calls ``pending_reminders`` / ``mark_reminded``. Texts live in ``STRINGS``
(merged into the router's ``_STRINGS``).
"""

# ruff: noqa: E501
from __future__ import annotations

import calendar
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import now_local, today_local
from alfred.models import Expense, Member, Message, RecurringItem
from alfred.parsing import to_amount
from alfred.validation import MAX_AMOUNT

MAX_ACTIVE_ITEMS = 30
REMINDER_FROM_HOUR = 9  # local time: no 03:00 buzz
REMINDER_UNTIL_HOUR = 20
WINDOW_HOURS = 24  # WhatsApp free-form messages only inside this window after the user wrote

# ── parsing ──────────────────────────────────────────────────────────────────

_NAME = r"(?P<name>[a-z][a-z0-9 '&.\-]{1,60}?)"
_MONEY = r"(?:€\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|eur\b|euros?\b)?"
_LEAD = r"^(?:(?:adiciona|adicionar|add|cria|criar|nova|novo|voeg\s+toe|ajoute|fuege\s+hinzu)\s+(?:conta\s+fixa\s+|assinatura\s+|recurring\s+|vaste\s+last\s+)?)?"
_MONTHLY = re.compile(
    r"\b(?:todo\s+dia|todo\s+(?:o\s+)?mes|todos\s+os\s+meses|mensal(?:mente)?|mensais|por\s+mes|cada\s+mes|"
    r"every\s+month|monthly|a\s+month|per\s+month|every\s+\d{1,2}(?:st|nd|rd|th)\b|"
    r"elke\s+maand|maandelijks|per\s+maand|iedere\s+maand|"
    r"chaque\s+mois|tous\s+les\s+mois|mensuel(?:le)?|par\s+mois|"
    r"jeden\s+monat|monatlich|pro\s+monat)\b"
)
_YEARLY = re.compile(
    r"\b(?:anual(?:mente)?|anuais|por\s+ano|todo\s+ano|every\s+year|yearly|annual(?:ly)?|a\s+year|per\s+year|"
    r"jaarlijks|per\s+jaar|elk\s+jaar|chaque\s+annee|annuel(?:le)?|par\s+an|jaehrlich|jahrlich|pro\s+jahr)\b"
)
_WEEKLY = re.compile(
    r"\b(?:semanal(?:mente)?|toda\s+semana|por\s+semana|every\s+week|weekly|a\s+week|per\s+week|"
    r"wekelijks|per\s+week|elke\s+week|chaque\s+semaine|hebdomadaire|par\s+semaine|woechentlich|wochentlich|pro\s+woche)\b"
)
_DAY = re.compile(
    r"\b(?:dia|day|the|every|op\s+de|le|am|den|toda\s+dia)\s+(\d{1,2})(?:st|nd|rd|th|e|ste|de|er)?\b"
)
_INSTALLMENT_RE = re.compile(
    _LEAD
    + _NAME
    + r"\s+(?:(?:em|in|en|a|de)\s+)?(?P<n>\d{1,2})\s*(?:x|parcelas?|vezes|installments?|termijnen|fois|raten)\s*(?:de\s+|of\s+|van\s+|à\s+)?"
    + _MONEY
    + r"(?P<rest>(?:\s+.*)?)\s*[.!]*$"
)
_RECURRING_RE = re.compile(
    _LEAD + _NAME + r"\s+(?:de\s+)?" + _MONEY + r"(?P<rest>(?:\s+.*)?)\s*[.!]*$"
)

_CATEGORY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("netflix", "spotify", "disney", "hbo", "youtube", "prime", "apple", "icloud", "deezer", "assinatura", "abonnement", "subscription"), "abonnement"),
    (("aluguel", "huur", "rent", "loyer", "miete", "condominio", "hypotheek", "hipoteca", "mortgage", "luz", "energia", "energie", "gas", "agua", "water", "internet", "wifi", "strom"), "wonen"),
    (("seguro", "zorgverzekering", "verzekering", "insurance", "assurance", "versicherung", "plano de saude", "ginasio", "academia", "gym", "fitness", "sportschool"), "gezondheid"),
)  # fmt: skip


@dataclass(frozen=True)
class Parsed:
    name: str
    amount: float
    frequency: str  # monthly | yearly | weekly
    due_day: int | None
    installments_total: int | None
    category: str
    kind: str  # fixed | subscription | installment


def guess_category(name_plain: str) -> str:
    for words, cat in _CATEGORY_HINTS:
        if any(re.search(rf"\b{re.escape(w)}\b", name_plain) for w in words):
            return cat
    return "overig"


def parse_recurring(body: str, body_plain: str) -> Parsed | None:
    """A "rent 1200 every 1st" / "netflix 13,99 mensal" / "celular em 10x de 89,90" message."""
    plain = body_plain.strip()
    m = _INSTALLMENT_RE.match(plain)
    total = None
    frequency = "monthly"
    if m:
        total = int(m.group("n"))
        if not 2 <= total <= 60:
            return None
        rest = m.group("rest") or ""
    else:
        m = _RECURRING_RE.match(plain)
        if not m:
            return None
        rest = m.group("rest") or ""
        if _YEARLY.search(rest):
            frequency = "yearly"
        elif _WEEKLY.search(rest):
            frequency = "weekly"
        elif not _MONTHLY.search(rest):
            return None
    amount = to_amount(m.group("amt"))
    if amount is None or amount > MAX_AMOUNT:
        return None
    day_m = _DAY.search(rest)
    due_day = int(day_m.group(1)) if day_m else None
    if due_day is not None and not 1 <= due_day <= 31:
        return None
    name = body[m.start("name") : m.end("name")].strip(" .-'&")
    if not name:
        return None
    name = name[0].upper() + name[1:]
    cat = guess_category(m.group("name"))
    kind = "installment" if total else "subscription" if cat == "abonnement" else "fixed"
    return Parsed(
        name, amount, frequency, due_day if frequency == "monthly" else None, total, cat, kind
    )


# ── dates ────────────────────────────────────────────────────────────────────


def clamp_day(year: int, month: int, day: int) -> date:
    """Day 29–31 in a short month falls on that month's last day."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + n
    return idx // 12, idx % 12 + 1


def first_due(frequency: str, due_day: int | None, today: date) -> date:
    """First due date: the next occurrence of an explicit day, else one period from today."""
    if frequency == "weekly":
        return today + timedelta(days=7)
    if frequency == "yearly":
        return clamp_day(today.year + 1, today.month, today.day)
    if due_day is None:
        y, mo = _add_months(today.year, today.month, 1)
        return clamp_day(y, mo, today.day)
    cand = clamp_day(today.year, today.month, due_day)
    if cand < today:
        y, mo = _add_months(today.year, today.month, 1)
        cand = clamp_day(y, mo, due_day)
    return cand


def advance(current: date, frequency: str, due_day: int | None) -> date:
    """The due date after ``current``."""
    if frequency == "weekly":
        return current + timedelta(days=7)
    if frequency == "yearly":
        return clamp_day(current.year + 1, current.month, current.day)
    y, mo = _add_months(current.year, current.month, 1)
    return clamp_day(y, mo, due_day or current.day)


def monthly_equivalent(amount: float, frequency: str) -> Decimal:
    a = Decimal(str(amount))
    if frequency == "yearly":
        a = a / 12
    elif frequency == "weekly":
        a = a * 52 / 12
    return a.quantize(Decimal("0.01"), ROUND_HALF_UP)


# ── name matching ────────────────────────────────────────────────────────────

_STOP = {
    "de",
    "do",
    "da",
    "o",
    "a",
    "os",
    "as",
    "the",
    "het",
    "le",
    "la",
    "die",
    "der",
    "das",
    "conta",
    "fixa",
    "meu",
    "minha",
    "my",
    "mijn",
    "mon",
    "mein",
    "l",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in _STOP}


def match_items(typed: str, items: list[RecurringItem], *, loose: bool) -> list[RecurringItem]:
    """Items whose name fits what the user typed. ``loose`` also accepts extra typed words
    ("paguei o aluguel de outubro"); strict requires the typed words to be inside the name."""
    from alfred.parsing import strip_accents

    want = _tokens(strip_accents(typed))
    if not want:
        return []
    exact, partial = [], []
    for it in items:
        have = _tokens(strip_accents(it.name))
        if not have:
            continue
        if want == have:
            exact.append(it)
        elif want <= have or (loose and have <= want):
            partial.append(it)
    return exact or partial


# ── commands ─────────────────────────────────────────────────────────────────

_LIST_WORDS = {
    "contas fixas", "minhas contas fixas", "gastos fixos", "meus gastos fixos", "despesas fixas",
    "minhas despesas fixas", "my recurring", "recurring", "recurring bills", "my recurring bills",
    "fixed costs", "my fixed costs", "vaste lasten", "mijn vaste lasten", "charges fixes",
    "mes charges fixes", "fixkosten", "meine fixkosten",
}  # fmt: skip
_PAID_RE = re.compile(
    r"^(?:(?:ja\s+)?paguei|pago|paid|i\s+paid|betaald|j'ai\s+paye|ich\s+habe|bezahlt)\s+"
    r"(?:(?:a|o|as|os|the|de|het|le|la|l'|die|der|das|my|meu|minha)\s+)?(?P<name>[a-z][a-z '\-]{1,60}?)(?:\s+bezahlt)?\s*[.!]*$"
)  # fmt: skip
_PAID_NL_RE = re.compile(
    r"^ik\s+heb\s+(?:de\s+|het\s+)?(?P<name>[a-z][a-z '\-]{1,60}?)\s+betaald\s*[.!]*$"
)
_CANCEL_RE = re.compile(
    r"^(?:cancela|cancele|cancelar|apaga|apague|apagar|remove|remova|remover|tira|tire|"
    r"cancel|stop|delete|annuleer|verwijder|stop\s+met|annule|supprime|loesche|kuendige)\s+"
    r"(?:(?:a|o|the|het|de|le|la|das|die|der|my|meu|minha|mijn|mon|mein)\s+)?"
    r"(?:(?:conta\s+fixa|assinatura|subscription|abonnement|recurring|vaste\s+last)\s+)?(?P<name>[a-z][a-z '\-]{1,60}?)\s*[.!]*$"
)  # fmt: skip


def _fmt_date(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


async def _active_items(session: AsyncSession, member_id: uuid.UUID) -> list[RecurringItem]:
    return list(
        (
            await session.execute(
                select(RecurringItem)
                .where(RecurringItem.member_id == member_id, RecurringItem.active.is_(True))
                .order_by(RecurringItem.next_due_date, RecurringItem.name)
            )
        )
        .scalars()
        .all()
    )


async def handle_recurring_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    """Reply for create / list / "paguei X" / cancel, or None when it is not one of those."""
    from alfred.conversation import _t

    plain = re.sub(r"\s+", " ", body_plain).strip(" .!?")
    if plain in _LIST_WORDS:
        return await _list(member, lang, session)

    parsed = parse_recurring(body, body_plain)
    if parsed is not None:
        return await _create(parsed, member, lang, session)

    m_paid = _PAID_RE.match(plain) or _PAID_NL_RE.match(plain)
    if m_paid and not any(c.isdigit() for c in plain):
        items = await _active_items(session, member.id)
        hits = match_items(m_paid.group("name"), items, loose=True)
        if len(hits) == 1:
            return await _pay(hits[0], member, lang, session)
        if len(hits) > 1:
            return _t("recurring_ambiguous", lang, names=", ".join(h.name for h in hits))
        return None

    m_cancel = _CANCEL_RE.match(plain)
    if m_cancel:
        items = await _active_items(session, member.id)
        hits = match_items(m_cancel.group("name"), items, loose=False)
        if len(hits) == 1:
            name = hits[0].name
            await session.delete(hits[0])
            await session.flush()
            audit(session, "recurring_removed", member.id)
            return _t("recurring_removed", lang, name=name)
        if len(hits) > 1:
            return _t("recurring_ambiguous", lang, names=", ".join(h.name for h in hits))
        return None
    return None


async def _create(p: Parsed, member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _fmt_eur, _t

    count = await session.scalar(
        select(func.count())
        .select_from(RecurringItem)
        .where(RecurringItem.member_id == member.id, RecurringItem.active.is_(True))
    )
    if (count or 0) >= MAX_ACTIVE_ITEMS:
        return _t("recurring_limit", lang, n=MAX_ACTIVE_ITEMS)
    today = today_local()
    due = first_due(p.frequency, p.due_day, today)
    session.add(
        RecurringItem(
            id=uuid.uuid4(),
            member_id=member.id,
            household_id=member.household_id,
            name=p.name,
            amount=p.amount,
            category=p.category,
            kind=p.kind,
            frequency=p.frequency,
            due_day=p.due_day,
            next_due_date=due,
            installments_total=p.installments_total,
            installments_paid=0,
        )
    )
    await session.flush()
    audit(session, "recurring_created", member.id)
    if p.installments_total:
        return _t(
            "recurring_created_installments",
            lang,
            name=p.name,
            n=p.installments_total,
            amount=_fmt_eur(p.amount),
            due=_fmt_date(due),
        )
    return _t(
        "recurring_created",
        lang,
        name=p.name,
        amount=_fmt_eur(p.amount),
        freq=_t(f"recurring_freq_{p.frequency}", lang),
        due=_fmt_date(due),
    )


async def _pay(item: RecurringItem, member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.budgets import alert_after_expense
    from alfred.conversation import _fmt_eur, _t

    when = now_local()
    session.add(
        Expense(
            id=uuid.uuid4(),
            member_id=member.id,
            household_id=member.household_id,
            transaction_type="expense",
            amount=item.amount,
            currency="EUR",
            merchant=item.name,
            category=item.category or "overig",
            description=item.name,
            expense_date=when,
            status="paid",
        )
    )
    item.installments_paid = (item.installments_paid or 0) + 1
    finished = bool(item.installments_total and item.installments_paid >= item.installments_total)
    if finished:
        item.active = False
    else:
        item.next_due_date = advance(item.next_due_date, item.frequency, item.due_day)
    session.add(item)
    await session.flush()
    audit(session, "recurring_paid", member.id)
    if finished:
        reply = _t("recurring_paid_last", lang, name=item.name, amount=_fmt_eur(item.amount))
    elif item.installments_total:
        reply = _t(
            "recurring_paid_installment",
            lang,
            name=item.name,
            amount=_fmt_eur(item.amount),
            k=item.installments_paid,
            n=item.installments_total,
            due=_fmt_date(item.next_due_date),
        )
    else:
        reply = _t(
            "recurring_paid",
            lang,
            name=item.name,
            amount=_fmt_eur(item.amount),
            due=_fmt_date(item.next_due_date),
        )
    return reply + await alert_after_expense(session, member, item.category, when, lang)


async def _list(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _fmt_eur, _t

    items = await _active_items(session, member.id)
    if not items:
        return _t("recurring_list_empty", lang)
    lines = [_t("recurring_list_header", lang, n=len(items))]
    total = Decimal("0")
    for it in items:
        total += monthly_equivalent(it.amount, it.frequency)
        extra = ""
        if it.installments_total:
            left = it.installments_total - (it.installments_paid or 0)
            extra = _t("recurring_row_installments", lang, left=left, n=it.installments_total)
        lines.append(
            _t(
                "recurring_row",
                lang,
                name=it.name,
                amount=_fmt_eur(it.amount),
                due=_fmt_date(it.next_due_date),
                extra=extra,
            )
        )
    lines.append(_t("recurring_list_total", lang, total=_fmt_eur(float(total))))
    return "\n".join(lines)


# ── cron side ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Reminder:
    item_id: uuid.UUID
    due: date
    wa_phone: str
    lang: str
    text: str
    in_window: bool  # the member wrote in the last 24 h: free-form text is allowed


def reminder_text(name: str, amount: float, due: date, today: date, lang: str) -> str:
    from alfred.conversation import _fmt_eur, _t

    days = (due - today).days
    if days < 0:
        when = _t("recurring_when_overdue", lang, date=_fmt_date(due))
    elif days == 0:
        when = _t("recurring_when_today", lang)
    elif days == 1:
        when = _t("recurring_when_tomorrow", lang)
    else:
        when = _t("recurring_when_days", lang, n=days)
    return _t("recurring_reminder", lang, name=name, amount=_fmt_eur(amount), when=when)


async def pending_reminders(session: AsyncSession, now: datetime) -> list[Reminder]:
    """Bills whose reminder is due and not yet sent for the current due date."""
    if not REMINDER_FROM_HOUR <= now.hour < REMINDER_UNTIL_HOUR:
        return []
    today = now.date()
    rows = (
        await session.execute(
            select(RecurringItem, Member.wa_phone, Member.language)
            .join(Member, RecurringItem.member_id == Member.id)
            .where(
                RecurringItem.active.is_(True),
                Member.consent_state == "accepted",
                (RecurringItem.last_reminded_for.is_(None))
                | (RecurringItem.last_reminded_for != RecurringItem.next_due_date),
            )
        )
    ).all()
    out: list[Reminder] = []
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    for item, phone, lang in rows:
        if today < item.next_due_date - timedelta(days=item.remind_days_before):
            continue
        if item.end_date and item.next_due_date > item.end_date:
            continue
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == item.member_id, Message.direction == "inbound"
            )
        )
        lang = lang or "en"
        out.append(
            Reminder(
                item_id=item.id,
                due=item.next_due_date,
                wa_phone=phone,
                lang=lang,
                text=reminder_text(item.name, item.amount, item.next_due_date, today, lang),
                in_window=bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff),
            )
        )
    return out


async def mark_reminded(session: AsyncSession, item_id: uuid.UUID, due: date) -> None:
    item = await session.get(RecurringItem, item_id)
    if item is not None:
        item.last_reminded_for = due
        session.add(item)


_FREQ = {
    "monthly": ("mensal", "maandelijks", "monthly", "mensuel", "monatlich"),
    "yearly": ("anual", "jaarlijks", "yearly", "annuel", "jährlich"),
    "weekly": ("semanal", "wekelijks", "weekly", "hebdomadaire", "wöchentlich"),
}
_L = ("pt", "nl", "en", "fr", "de")


def _freq(key: str) -> dict[str, str]:
    return dict(zip(_L, _FREQ[key], strict=True))


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "recurring_freq_monthly": _freq("monthly"),
    "recurring_freq_yearly": _freq("yearly"),
    "recurring_freq_weekly": _freq("weekly"),
    "recurring_created": {
        "pt": ("Anotado: {name}, {amount} ({freq}). Próximo vencimento: {due}. Aviso 3 dias antes.", "Fechado: {name} de {amount} ({freq}). Vence em {due}; eu aviso 3 dias antes."),
        "nl": ("Genoteerd: {name}, {amount} ({freq}). Volgende vervaldatum: {due}. Ik herinner je 3 dagen van tevoren.", "Afgesproken: {name} van {amount} ({freq}). Vervalt op {due}; ik waarschuw je 3 dagen eerder."),
        "en": ("Noted: {name}, {amount} ({freq}). Next due: {due}. I'll remind you 3 days before.", "Done: {name} at {amount} ({freq}). Due on {due}; I'll remind you 3 days before."),
        "fr": ("C'est noté : {name}, {amount} ({freq}). Prochaine échéance : {due}. Je te préviens 3 jours avant.", "Parfait : {name} de {amount} ({freq}). Échéance le {due} ; je te préviens 3 jours avant."),
        "de": ("Notiert: {name}, {amount} ({freq}). Nächste Fälligkeit: {due}. Ich erinnere dich 3 Tage vorher.", "Erledigt: {name} über {amount} ({freq}). Fällig am {due}; ich erinnere dich 3 Tage vorher."),
    },
    "recurring_created_installments": {
        "pt": "Anotado: {name} em {n}x de {amount}. Primeira parcela: {due}. Aviso 3 dias antes de cada uma.",
        "nl": "Genoteerd: {name} in {n}x {amount}. Eerste termijn: {due}. Ik herinner je 3 dagen van tevoren.",
        "en": "Noted: {name} in {n}x of {amount}. First instalment: {due}. I'll remind you 3 days before each.",
        "fr": "C'est noté : {name} en {n}x de {amount}. Première échéance : {due}. Je te préviens 3 jours avant chacune.",
        "de": "Notiert: {name} in {n}x {amount}. Erste Rate: {due}. Ich erinnere dich jeweils 3 Tage vorher.",
    },
    "recurring_paid": {
        "pt": ("Anotei o pagamento: {name}, {amount}. Próximo vencimento: {due}.", "Pago: {name}, {amount}. Próximo vencimento: {due}."),
        "nl": ("Betaling genoteerd: {name}, {amount}. Volgende vervaldatum: {due}.", "Betaald: {name}, {amount}. Volgende vervaldatum: {due}."),
        "en": ("Payment logged: {name}, {amount}. Next due: {due}.", "Paid: {name}, {amount}. Next due: {due}."),
        "fr": ("Paiement noté : {name}, {amount}. Prochaine échéance : {due}.", "Payé : {name}, {amount}. Prochaine échéance : {due}."),
        "de": ("Zahlung notiert: {name}, {amount}. Nächste Fälligkeit: {due}.", "Bezahlt: {name}, {amount}. Nächste Fälligkeit: {due}."),
    },
    "recurring_paid_installment": {
        "pt": "Anotei o pagamento: {name}, {amount} (parcela {k}/{n}). Próxima: {due}.",
        "nl": "Betaling genoteerd: {name}, {amount} (termijn {k}/{n}). Volgende: {due}.",
        "en": "Payment logged: {name}, {amount} (instalment {k}/{n}). Next: {due}.",
        "fr": "Paiement noté : {name}, {amount} (échéance {k}/{n}). Prochaine : {due}.",
        "de": "Zahlung notiert: {name}, {amount} (Rate {k}/{n}). Nächste: {due}.",
    },
    "recurring_paid_last": {
        "pt": "Anotei o pagamento: {name}, {amount}. Foi a última parcela 🎉",
        "nl": "Betaling genoteerd: {name}, {amount}. Dat was de laatste termijn 🎉",
        "en": "Payment logged: {name}, {amount}. That was the last instalment 🎉",
        "fr": "Paiement noté : {name}, {amount}. C'était la dernière échéance 🎉",
        "de": "Zahlung notiert: {name}, {amount}. Das war die letzte Rate 🎉",
    },
    "recurring_removed": {
        "pt": "Conta fixa removida: {name}.",
        "nl": "Vaste last verwijderd: {name}.",
        "en": "Recurring item removed: {name}.",
        "fr": "Charge fixe supprimée : {name}.",
        "de": "Fixkosten entfernt: {name}.",
    },
    "recurring_ambiguous": {
        "pt": "Mais de uma conta combina: {names}. Diga o nome completo.",
        "nl": "Meerdere vaste lasten passen: {names}. Geef de volledige naam.",
        "en": "More than one item matches: {names}. Please use the full name.",
        "fr": "Plusieurs charges correspondent : {names}. Donne le nom complet.",
        "de": "Mehrere Fixkosten passen: {names}. Bitte den vollen Namen nennen.",
    },
    "recurring_limit": {
        "pt": "Você já tem {n} contas fixas, que é o limite. Remova alguma para adicionar outra.",
        "nl": "Je hebt al {n} vaste lasten, dat is het maximum. Verwijder er eerst een.",
        "en": "You already have {n} recurring items, which is the limit. Remove one to add another.",
        "fr": "Tu as déjà {n} charges fixes, c'est la limite. Supprime-en une pour en ajouter.",
        "de": "Du hast schon {n} Fixkosten, das ist das Maximum. Entferne eine, um eine neue anzulegen.",
    },
    "recurring_list_empty": {
        "pt": "Você ainda não tem contas fixas. Para criar, diga por exemplo “aluguel 1200 todo dia 1” ou “celular em 10x de 89,90”.",
        "nl": "Je hebt nog geen vaste lasten. Maak er een met bijvoorbeeld “huur 1200 elke maand op de 1e”.",
        "en": "You don't have any recurring items yet. Create one, for example “rent 1200 monthly on the 1st” or “phone in 10x of 89.90”.",
        "fr": "Tu n'as pas encore de charges fixes. Crée-en une, par exemple « loyer 1200 chaque mois le 1 ».",
        "de": "Du hast noch keine Fixkosten. Lege eine an, zum Beispiel „Miete 1200 monatlich am 1.“.",
    },
    "recurring_list_header": {
        "pt": "Suas contas fixas ({n}):",
        "nl": "Je vaste lasten ({n}):",
        "en": "Your recurring items ({n}):",
        "fr": "Tes charges fixes ({n}) :",
        "de": "Deine Fixkosten ({n}):",
    },
    "recurring_row": {
        "pt": "• {name}: {amount} · vence {due}{extra}",
        "nl": "• {name}: {amount} · vervalt {due}{extra}",
        "en": "• {name}: {amount} · due {due}{extra}",
        "fr": "• {name} : {amount} · échéance {due}{extra}",
        "de": "• {name}: {amount} · fällig {due}{extra}",
    },
    "recurring_row_installments": {
        "pt": " · faltam {left} de {n}",
        "nl": " · nog {left} van {n}",
        "en": " · {left} of {n} left",
        "fr": " · il reste {left} sur {n}",
        "de": " · noch {left} von {n}",
    },
    "recurring_list_total": {
        "pt": "Total por mês: {total}",
        "nl": "Totaal per maand: {total}",
        "en": "Total per month: {total}",
        "fr": "Total par mois : {total}",
        "de": "Gesamt pro Monat: {total}",
    },
    "recurring_reminder": {
        "pt": "⏰ {name} ({amount}) {when}.",
        "nl": "⏰ {name} ({amount}) {when}.",
        "en": "⏰ {name} ({amount}) {when}.",
        "fr": "⏰ {name} ({amount}) {when}.",
        "de": "⏰ {name} ({amount}) {when}.",
    },
    "recurring_when_today": {"pt": "vence hoje", "nl": "vervalt vandaag", "en": "is due today", "fr": "est à payer aujourd'hui", "de": "ist heute fällig"},
    "recurring_when_tomorrow": {"pt": "vence amanhã", "nl": "vervalt morgen", "en": "is due tomorrow", "fr": "est à payer demain", "de": "ist morgen fällig"},
    "recurring_when_days": {"pt": "vence em {n} dias", "nl": "vervalt over {n} dagen", "en": "is due in {n} days", "fr": "est à payer dans {n} jours", "de": "ist in {n} Tagen fällig"},
    "recurring_when_overdue": {"pt": "venceu em {date}", "nl": "was vervallen op {date}", "en": "was due on {date}", "fr": "était à payer le {date}", "de": "war fällig am {date}"},
}  # fmt: skip
