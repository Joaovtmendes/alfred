# ruff: noqa: E501
"""V2-16 — states of an entry: paid · to pay · received · to receive.

Only settled entries (``paid`` / ``received``) count in balance, totals and reports. What is still
to pay or to receive is shown apart ("forecast") and, when a to-pay is overdue by more than a day,
as a warning in the monthly summary and in the list.

Messages (rule-based, no LLM): "a pagar luz 120 dia 10" · "a receber 300 do freela dia 15" ·
"paguei a luz" / "recebi do freela" (changes the state of the matching pending entry, never
duplicates it) · "o que tenho a pagar" / "o que tenho a receber".
A "paguei X" with no pending entry called X is *not* ours, so the fixed-bills and debts handlers
still get it.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.agenda import parse_when
from alfred.audit import audit
from alfred.clock import local_tz, now_local, today_local
from alfred.models import PENDING, SETTLED, Expense, Member
from alfred.parsing import strip_accents, to_amount
from alfred.validation import MAX_AMOUNT

TO_PAY = "to_pay"
TO_RECEIVE = "to_receive"
MAX_PENDING = 100
OVERDUE_AFTER_DAYS = 1

__all__ = ["SETTLED", "PENDING", "pending_summary", "handle_ledger_command", "STRINGS"]

_MARK_PAY = r"(?:a pagar|para pagar|te betalen|nog te betalen|to pay|to be paid|a payer|zu zahlen|zu bezahlen)"
_MARK_RECV = r"(?:a receber|para receber|te ontvangen|nog te ontvangen|to receive|to be received|a recevoir|zu erhalten|zu bekommen)"
_CREATE_PAY = re.compile(rf"^{_MARK_PAY}\s+(?P<rest>.+)$")
_CREATE_RECV = re.compile(rf"^{_MARK_RECV}\s+(?P<rest>.+)$")
# "luz 80 a pagar dia 5": the marker after the name and amount
_MID_PAY = re.compile(rf"^(?P<pre>.+?)\s+{_MARK_PAY}(?=\s|$)(?P<post>.*)$")
_MID_RECV = re.compile(rf"^(?P<pre>.+?)\s+{_MARK_RECV}(?=\s|$)(?P<post>.*)$")
_QUESTION_LEAD = (
    "quanto",
    "tenho",
    "o que",
    "quais",
    "qual",
    "what",
    "how",
    "wat",
    "hoeveel",
    "combien",
    "was ",
    "wie ",
)
_AMT = re.compile(r"(?:€\s*)?(\d[\d.,]*)\s*(?:€|eur\b|euros?\b)?")
_CONNECTORS = {
    "do", "da", "de", "dos", "das", "from", "van", "von", "vom", "du", "des", "der", "die", "voor",
    "pour", "fur", "para", "pra", "pro", "the", "o", "a", "as", "os", "le", "la", "les", "het", "een", "um", "uma", "on", "em", "op", "am", "au", "aux", "ao", "aos",
}  # fmt: skip
_PAID_VERBS = {"paguei", "paid", "betaald", "betaalde", "paye", "bezahlt", "bezahlte"}
_RECV_VERBS = {"recebi", "received", "ontvangen", "recu", "erhalten", "bekommen", "recebido"}
_FILLER = {
    "i",
    "ik",
    "heb",
    "j",
    "ai",
    "ich",
    "habe",
    "my",
    "mijn",
    "mon",
    "mein",
    "meu",
    "minha",
    "got",
    # words around the name that do not identify the bill: "já paguei a conta de luz"
    "ja",
    "conta",
    "contas",
    "boleto",
    "fatura",
    "hoje",
    "ontem",
    "agora",
    "already",
    "bill",
    "today",
    "yesterday",
    "al",
    "rekening",
    "vandaag",
    "gisteren",
    "deja",
    "facture",
    "hier",
    "schon",
    "rechnung",
    "heute",
    "gestern",
}
_LIST_PAY = {
    "o que tenho a pagar", "o que eu tenho a pagar", "a pagar", "contas a pagar", "o que falta pagar",
    "wat moet ik nog betalen", "nog te betalen", "te betalen", "what do i have to pay", "what do i still have to pay",
    "to pay", "what is pending", "ce que j'ai a payer", "ce que je dois payer", "a payer", "was muss ich noch zahlen",
    "was ist noch zu zahlen", "zu zahlen",
}  # fmt: skip
_LIST_RECV = {
    "o que tenho a receber", "o que eu tenho a receber", "a receber", "o que falta receber",
    "wat krijg ik nog", "nog te ontvangen", "te ontvangen", "what do i have to receive", "what am i still owed",
    "to receive", "ce que j'ai a recevoir", "a recevoir", "was bekomme ich noch", "was steht noch aus", "zu erhalten",
}  # fmt: skip


_HOUSING_WORDS = {
    "luz", "energia", "eletricidade", "agua", "gas", "aluguel", "renda", "condominio", "stroom",
    "water", "huur", "electricity", "rent", "hypotheek", "hipoteca", "mortgage", "loyer",
    "electricite", "miete", "strom",
}  # fmt: skip
_SUBSCRIPTION_WORDS = {
    "internet", "netflix", "spotify", "telefone", "telefoon", "phone", "mobile", "celular",
    "assinatura", "abonnement", "streaming", "disney", "youtube", "icloud", "wifi",
}  # fmt: skip


def _bill_category(name: str) -> str:
    """Category of a bill from its name: utilities and rent are housing, internet and phone are
    subscriptions, anything else falls back to the generic resolver, then Other."""
    from alfred.budgets import resolve_category

    words = set(re.findall(r"[a-z]+", strip_accents(name.lower())))
    if words & _HOUSING_WORDS:
        return "wonen"
    if words & _SUBSCRIPTION_WORDS:
        return "abonnement"
    return resolve_category(name) or "overig"


def _dec(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fmt(value) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for a, b in sorted(spans, reverse=True):
        text = text[:a] + " " + text[b:]
    return text


def _name_from(body_rest: str, plain_rest: str, spans: list[tuple[int, int]]) -> str | None:
    """The thing's name as the member wrote it, minus date/amount pieces and connector words."""
    if len(body_rest) != len(plain_rest):
        body_rest = plain_rest
    words = _cut(body_rest, spans).split()
    while words and strip_accents(words[0].lower()).strip(".,") in _CONNECTORS:
        words.pop(0)
    while words and strip_accents(words[-1].lower()).strip(".,") in _CONNECTORS:
        words.pop()
    name = " ".join(words).strip(" .,-")[:80]
    return name[:1].upper() + name[1:] if name else None


@dataclass
class NewPending:
    kind: str  # to_pay | to_receive
    name: str
    amount: float
    due: date


def parse_pending(body: str, body_plain: str, today: date) -> NewPending | str | None:
    """A new pending entry, "bad_amount"/"no_name" when it is clearly one but unusable, else None."""
    plain = " ".join(body_plain.split())
    body = " ".join(body.split())
    kind = TO_PAY
    m = _CREATE_PAY.match(plain)
    if not m:
        kind, m = TO_RECEIVE, _CREATE_RECV.match(plain)
    mid = None
    if not m:
        kind, mid = TO_PAY, _MID_PAY.match(plain)
        if not mid:
            kind, mid = TO_RECEIVE, _MID_RECV.match(plain)
        if not mid or plain.startswith(_QUESTION_LEAD) or not re.search(r"\d", plain):
            return None
    if mid is not None:
        cut = lambda t: (t[: mid.end("pre")] + t[mid.start("post") :]).strip()  # noqa: E731
        plain_rest = cut(plain)
        body_rest = cut(body) if len(body) == len(plain) else plain_rest
    else:
        start = m.start("rest")
        plain_rest, body_rest = (
            plain[start:],
            body[start:] if len(body) == len(plain) else plain[start:],
        )
    when = parse_when(plain_rest, datetime.combine(today, time(0, 0), tzinfo=local_tz()))
    spans = list(when.spans)
    cleaned = _cut(plain_rest, spans)
    # the amount is the first number left once the date pieces are cut out
    am = _AMT.search(cleaned)
    if not am:
        return "bad_amount"
    amount = to_amount(am.group(1))
    if amount is None or amount > MAX_AMOUNT:
        return "bad_amount"
    # find that amount again in the original text (same length as ``plain_rest``) to cut it
    for cand in _AMT.finditer(plain_rest):
        if cand.group(1) == am.group(1) and all(
            not (cand.start() < b and a < cand.end()) for a, b in spans
        ):
            spans.append(cand.span())
            break
    name = _name_from(body_rest, plain_rest, spans)
    if not name:
        return "no_name"
    return NewPending(kind, name, amount, when.day or today)


def _tokens(text: str) -> set[str]:
    return {
        t for t in re.findall(r"[a-z0-9]+", strip_accents(text.lower())) if t not in _CONNECTORS
    }


def parse_settle(body_plain: str) -> tuple[str, set[str], float | None] | None:
    """("pay"|"recv", name tokens, amount) for "paguei a luz" / "recebi 300 do freela"."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 80:
        return None
    words = set(re.findall(r"[a-z']+", plain))
    kind = "pay" if words & _PAID_VERBS else "recv" if words & _RECV_VERBS else None
    if kind is None:
        return None
    amount = None
    if am := _AMT.search(plain):
        amount = to_amount(am.group(1))
        plain = plain[: am.start()] + " " + plain[am.end() :]
    tokens = _tokens(plain) - _PAID_VERBS - _RECV_VERBS - _FILLER - {"i", "paid", "d"}
    if not tokens:
        return None
    return kind, tokens, amount


async def _pending(
    session: AsyncSession, member_id: uuid.UUID, kind: str | None = None
) -> list[Expense]:
    stmt = select(Expense).where(
        Expense.member_id == member_id, Expense.status.in_(PENDING if kind is None else (kind,))
    )
    return list(
        (await session.execute(stmt.order_by(Expense.expense_date, Expense.created_at))).scalars()
    )


def _label(e: Expense) -> str:
    return e.merchant or e.description or "—"


@dataclass(frozen=True)
class PendingSummary:
    to_pay: Decimal
    to_receive: Decimal
    overdue_count: int
    overdue_total: Decimal


async def pending_summary(
    session: AsyncSession, member_id: uuid.UUID, today: date | None = None
) -> PendingSummary:
    today = today or today_local()
    limit = datetime.combine(
        today - timedelta(days=OVERDUE_AFTER_DAYS), time.min, tzinfo=local_tz()
    )
    rows = await _pending(session, member_id)
    pay = sum((_dec(e.amount) for e in rows if e.status == TO_PAY), Decimal(0))
    recv = sum((_dec(e.amount) for e in rows if e.status == TO_RECEIVE), Decimal(0))
    late = [e for e in rows if e.status == TO_PAY and e.expense_date < limit]
    return PendingSummary(pay, recv, len(late), sum((_dec(e.amount) for e in late), Decimal(0)))


async def handle_ledger_command(
    body: str, body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = " ".join(body_plain.split()).strip(" .!?")
    if plain in _LIST_PAY:
        return await _list(member, lang, session, TO_PAY)
    if plain in _LIST_RECV:
        return await _list(member, lang, session, TO_RECEIVE)

    new = parse_pending(body, body_plain, today_local())
    if new == "bad_amount":
        return _t("ledger_bad_amount", lang)
    if new == "no_name":
        return _t("ledger_no_name", lang)
    if isinstance(new, NewPending):
        return await _create(new, member, lang, session)

    settle = parse_settle(body_plain)
    if settle is not None:
        return await _settle(settle, member, lang, session)
    return None


async def _create(new: NewPending, member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    if len(await _pending(session, member.id)) >= MAX_PENDING:
        return _t("ledger_limit", lang, n=MAX_PENDING)
    income = new.kind == TO_RECEIVE
    category = "inkomen" if income else _bill_category(new.name)
    session.add(
        Expense(
            id=uuid.uuid4(),
            member_id=member.id,
            household_id=member.household_id,
            transaction_type="income" if income else "expense",
            amount=new.amount,
            currency="EUR",
            merchant=new.name,
            category=category,
            expense_date=datetime.combine(new.due, time(12, 0), tzinfo=local_tz()),
            status=new.kind,
        )
    )
    await session.flush()
    audit(session, "pending_created", member.id, kind=new.kind)
    return _t(
        "ledger_added_recv" if income else "ledger_added_pay",
        lang,
        name=new.name,
        amount=_fmt(_dec(new.amount)),
        due=f"{new.due:%d/%m}",
    )


async def _list(member: Member, lang: str, session: AsyncSession, kind: str) -> str:
    from alfred.conversation import _t

    rows = await _pending(session, member.id, kind)
    if not rows:
        return _t("ledger_empty_pay" if kind == TO_PAY else "ledger_empty_recv", lang)
    today = today_local()
    lines = []
    for e in rows:
        due = e.expense_date.astimezone(local_tz()).date()
        late = (today - due).days
        tail = (
            f" — {_t('ledger_overdue', lang, days=late)}"
            if kind == TO_PAY and late > OVERDUE_AFTER_DAYS
            else ""
        )
        lines.append(f"• {_label(e)}: {_fmt(_dec(e.amount))} · {due:%d/%m}{tail}")
    total = sum((_dec(e.amount) for e in rows), Decimal(0))
    return (
        _t("ledger_header_pay" if kind == TO_PAY else "ledger_header_recv", lang, total=_fmt(total))
        + "\n"
        + "\n".join(lines)
    )


async def _settle(
    parsed: tuple[str, set[str], float | None], member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.budgets import alert_after_expense
    from alfred.conversation import _t

    kind, tokens, amount = parsed
    pending_kind = TO_PAY if kind == "pay" else TO_RECEIVE
    cands = [
        e for e in await _pending(session, member.id, pending_kind) if tokens <= _tokens(_label(e))
    ]
    if not cands:
        return None  # not ours: the debts / fixed bills / income handlers decide
    if len(cands) > 1 and amount is not None:
        exact = [e for e in cands if _dec(e.amount) == _dec(amount)]
        if exact:
            cands = exact[:1]
    if len(cands) > 1:
        options = ", ".join(f"{_label(e)} {_fmt(_dec(e.amount))}" for e in cands[:5])
        return _t("ledger_ambiguous", lang, options=options)
    entry = cands[0]
    if amount is not None and 0 < amount <= MAX_AMOUNT:
        entry.amount = float(_dec(amount))  # what was really paid / received
    entry.status = "paid" if kind == "pay" else "received"
    entry.expense_date = now_local()  # it counts when the money actually moved
    await session.flush()
    audit(session, "pending_settled", member.id, kind=entry.status)
    text = _t(
        "ledger_paid" if kind == "pay" else "ledger_received",
        lang,
        name=_label(entry),
        amount=_fmt(_dec(entry.amount)),
    )
    if kind == "pay":
        text += await alert_after_expense(session, member, entry.category, entry.expense_date, lang)
    return text


async def count_pending(session: AsyncSession, member_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Expense)
            .where(Expense.member_id == member_id, Expense.status.in_(PENDING))
        )
        or 0
    )


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "ledger_added_pay": _all(
        'Anotei a pagar: {name} {amount}, vence {due}. Não entra no saldo até você pagar ("paguei {name}").',
        'Genoteerd om te betalen: {name} {amount}, vervalt {due}. Telt pas mee in je saldo als je betaald hebt ("betaald {name}").',
        'Noted to pay: {name} {amount}, due {due}. It stays out of your balance until you pay it ("paid {name}").',
        "Noté à payer : {name} {amount}, échéance {due}. Ça n'entre pas dans le solde avant paiement (« payé {name} »).",
        'Zu zahlen notiert: {name} {amount}, fällig {due}. Es zählt erst im Saldo, wenn du bezahlt hast ("bezahlt {name}").',
    ),
    "ledger_added_recv": _all(
        'Anotei a receber: {name} {amount}, previsto para {due}. Não entra no saldo até você receber ("recebi {name}").',
        'Genoteerd om te ontvangen: {name} {amount}, verwacht op {due}. Telt pas mee in je saldo als je het hebt ontvangen ("ontvangen {name}").',
        'Noted to receive: {name} {amount}, expected {due}. It stays out of your balance until you receive it ("received {name}").',
        "Noté à recevoir : {name} {amount}, prévu le {due}. Ça n'entre pas dans le solde avant réception (« reçu {name} »).",
        'Zu erhalten notiert: {name} {amount}, erwartet am {due}. Es zählt erst im Saldo, wenn du es erhalten hast ("erhalten {name}").',
    ),
    "ledger_paid": _all(
        "Marcado como pago: {name} {amount}.",
        "Gemarkeerd als betaald: {name} {amount}.",
        "Marked as paid: {name} {amount}.",
        "Marqué comme payé : {name} {amount}.",
        "Als bezahlt markiert: {name} {amount}.",
    ),
    "ledger_received": _all(
        "Marcado como recebido: {name} {amount}.",
        "Gemarkeerd als ontvangen: {name} {amount}.",
        "Marked as received: {name} {amount}.",
        "Marqué comme reçu : {name} {amount}.",
        "Als erhalten markiert: {name} {amount}.",
    ),
    "ledger_ambiguous": _all(
        'Qual deles? Tenho: {options}. Diga o valor, por exemplo "paguei 120 da luz".',
        'Welke bedoel je? Ik heb: {options}. Noem het bedrag, bijvoorbeeld "120 betaald voor stroom".',
        'Which one? I have: {options}. Say the amount, for example "paid 120 for electricity".',
        "Lequel ? J'ai : {options}. Dis le montant, par exemple « payé 120 pour l'électricité ».",
        'Welchen meinst du? Ich habe: {options}. Nenn den Betrag, zum Beispiel "120 für Strom bezahlt".',
    ),
    "ledger_header_pay": _all(
        "A pagar: {total}",
        "Nog te betalen: {total}",
        "To pay: {total}",
        "À payer : {total}",
        "Noch zu zahlen: {total}",
    ),
    "ledger_header_recv": _all(
        "A receber: {total}",
        "Nog te ontvangen: {total}",
        "To receive: {total}",
        "À recevoir : {total}",
        "Noch zu erhalten: {total}",
    ),
    "ledger_overdue": _all(
        "atrasada há {days} dias",
        "{days} dagen te laat",
        "{days} days overdue",
        "en retard de {days} jours",
        "{days} Tage überfällig",
    ),
    "ledger_empty_pay": _all(
        "Nada a pagar por enquanto.",
        "Voorlopig niets te betalen.",
        "Nothing to pay for now.",
        "Rien à payer pour l'instant.",
        "Vorerst nichts zu zahlen.",
    ),
    "ledger_empty_recv": _all(
        "Nada a receber por enquanto.",
        "Voorlopig niets te ontvangen.",
        "Nothing to receive for now.",
        "Rien à recevoir pour l'instant.",
        "Vorerst nichts zu erhalten.",
    ),
    "ledger_bad_amount": _all(
        'Não entendi o valor. Exemplo: "a pagar luz 120 dia 10".',
        'Ik begrijp het bedrag niet. Voorbeeld: "te betalen stroom 120 op de 10e".',
        'I didn\'t get the amount. Example: "to pay electricity 120 on the 10th".',
        "Je n'ai pas compris le montant. Exemple : « à payer électricité 120 le 10 ».",
        'Den Betrag habe ich nicht verstanden. Beispiel: "zu zahlen Strom 120 am 10.".',
    ),
    "ledger_no_name": _all(
        'O que é? Exemplo: "a pagar luz 120 dia 10".',
        'Wat is het? Voorbeeld: "te betalen stroom 120 op de 10e".',
        'What is it? Example: "to pay electricity 120 on the 10th".',
        "C'est quoi ? Exemple : « à payer électricité 120 le 10 ».",
        'Was ist es? Beispiel: "zu zahlen Strom 120 am 10.".',
    ),
    "ledger_limit": _all(
        "Você já tem {n} pendências, que é o máximo. Pague ou apague algumas antes de anotar outras.",
        "Je hebt al {n} openstaande items, dat is het maximum. Betaal of verwijder er eerst een paar.",
        "You already have {n} pending items, which is the maximum. Pay or delete a few first.",
        "Tu as déjà {n} éléments en attente, c'est le maximum. Paies-en ou supprimes-en quelques-uns d'abord.",
        "Du hast schon {n} offene Einträge, das ist das Maximum. Zahle oder lösche zuerst einige.",
    ),
    "pending_forecast": _all(
        "Previsto: {pay} a pagar e {recv} a receber.",
        "Verwacht: {pay} te betalen en {recv} te ontvangen.",
        "Forecast: {pay} to pay and {recv} to receive.",
        "Prévu : {pay} à payer et {recv} à recevoir.",
        "Erwartet: {pay} zu zahlen und {recv} zu erhalten.",
    ),
    "pending_overdue": _all(
        'Atenção: {n} a pagar em atraso ({total}). Veja com "o que tenho a pagar".',
        'Let op: {n} betalingen te laat ({total}). Bekijk met "wat moet ik nog betalen".',
        'Heads up: {n} overdue to pay ({total}). See them with "what do i have to pay".',
        "Attention : {n} à payer en retard ({total}). Vois-les avec « ce que j'ai à payer ».",
        'Achtung: {n} überfällige Zahlungen ({total}). Sieh nach mit "was muss ich noch zahlen".',
    ),
}
