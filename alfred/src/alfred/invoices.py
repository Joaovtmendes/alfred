# ruff: noqa: E501
"""V2-12 part 2 — invoices the member issued to clients, tracked until paid.

The app does not issue invoices (numbering, KvK and VAT-number rules belong to invoicing software):
the member registers what they already sent. Rules only, no model call. Paying an invoice books a
business income entry, so the accounting tab and the VAT quarter pick it up.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import now_local
from alfred.models import ClientInvoice, Expense, Member
from alfred.parsing import strip_accents, to_amount
from alfred.training import Reply
from alfred.validation import MAX_AMOUNT

MAX_OPEN = 200
DEFAULT_TERM_DAYS = 30  # the usual Dutch payment term
MAX_TERM_DAYS = 365
RATES = (0, 9, 21)
_MARKUP = re.compile(r"[*_~`]")
_WORD = r"(?:fatura|faturas|invoice|invoices|factuur|facturen|facture|factures|rechnung|rechnungen)"
_ONE = r"(?:fatura|invoice|factuur|facture|rechnung)"
_ART = r"(?:(?:a|o|the|de|het|la|le|die|der|das)\s+)?"
_QUICK = re.compile(_WORD)

_LIST = {
    "faturas", "minhas faturas", "faturas abertas", "faturas em aberto", "faturas a receber",
    "recebiveis", "a receber de clientes", "invoices", "my invoices", "open invoices",
    "facturen", "mijn facturen", "openstaande facturen", "factures", "mes factures",
    "factures ouvertes", "rechnungen", "meine rechnungen", "offene rechnungen",
}  # fmt: skip

_CREATE = re.compile(
    r"^(?:(?:registra|registrar|nova|novo|emiti|emitir|criei|criar|new|create|issued|nieuwe|maak|cree|neue)\s+)?"
    rf"{_ONE}\s+"
    r"(?:(?:(?:n[o°º]?\.?|nr\.?|no\.?|numero|number|nummer)\s*|#)(?P<num>[a-z0-9][a-z0-9/_-]{0,19})\s+|(?P<num2>\d{2,4}[-/]\d{1,6})\s+)?"
    r"(?:para|pro|pra|to|voor|pour|an|de|from|van|von)\s+(?P<client>.+?)\s+"
    r"(?:(?:de|of|van|von|for|pour)\s+)?(?:€\s*)?(?P<amt>\d[\d.,]*)\s*(?:€|eur|euros?)?"
    r"(?:\s+(?:com\s+|with\s+|mit\s+|avec\s+|met\s+)?(?:btw|iva|vat|tva|mwst)\s*(?P<btw>\d{1,2})\s*%?)?"
    r"(?:\s+(?:vence|vencimento|due|vervalt|echeance|faellig|em|in|within|binnen|dans|innerhalb)\s+(?P<due>.+))?$"
)  # fmt: skip
_PAID_A = re.compile(
    rf"^{_ONE}\s+(?P<ref>.+?)\s+(?:paga|pago|foi paga|paid|betaald|payee|bezahlt)$"
)
_PAID_B = re.compile(rf"^(?P<ref>.+?)\s+(?:pagou|paid|betaalde|a paye|bezahlte)\s+{_ART}{_ONE}$")
_PAID_C = re.compile(rf"^(?:recebi|received|ontvangen|recu|erhalten)\s+{_ART}{_ONE}\s+(?P<ref>.+)$")
_DELETE = re.compile(
    rf"^(?:apaga|apagar|delete|remove|verwijder|supprime|loesche|loeschen)\s+{_ART}{_ONE}\s+(?P<ref>.+)$"
)
_DAYS = re.compile(r"^(\d{1,3})\s*(?:dias?|days?|dagen|jours?|tage[n]?)$")
_DATE = re.compile(
    r"^(?:dia\s+|op\s+|le\s+|am\s+|on\s+)?(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?$"
)


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _fmt(value: float | Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


def parse_due(text: str | None, issued: date) -> date | None:
    """Due date from "30 dias" / "15/11" / "15/11/2026"; the default term when ``text`` is empty."""
    if not text or not text.strip():
        return issued + timedelta(days=DEFAULT_TERM_DAYS)
    value = " ".join(text.split()).strip(" .!?")
    value = re.sub(r"^(?:em|in|over|within|binnen|dans|innerhalb|na)\s+", "", value)
    if m := _DAYS.match(value):
        days = int(m.group(1))
        return issued + timedelta(days=days) if 0 < days <= MAX_TERM_DAYS else None
    if m := _DATE.match(value):
        day, month = int(m.group(1)), int(m.group(2))
        year = m.group(3)
        try:
            if year:
                y = int(year)
                due = date(y + 2000 if y < 100 else y, month, day)
            else:
                due = date(issued.year, month, day)
                if due < issued:
                    due = date(issued.year + 1, month, day)
        except ValueError:
            return None
        return due if issued <= due <= issued + timedelta(days=MAX_TERM_DAYS) else None
    return None


def _client(raw_norm: str, plain: str, span: tuple[int, int]) -> str | None:
    """The client's name with the member's own capitalisation, markup stripped and capped."""
    text = raw_norm[span[0] : span[1]] if len(raw_norm) == len(plain) else plain[span[0] : span[1]]
    out = " ".join(_MARKUP.sub("", text).split())[:60].strip(" -,.")
    return out or None


def _key(text: str) -> str:
    return strip_accents(unicodedata.normalize("NFC", text)).lower().strip()


async def _all(session: AsyncSession, member: Member) -> list[ClientInvoice]:
    rows = await session.execute(
        select(ClientInvoice)
        .where(ClientInvoice.member_id == member.id)
        .order_by(ClientInvoice.due_on, ClientInvoice.created_at)
        .limit(MAX_OPEN * 3)
    )
    return list(rows.scalars().all())


def _match(rows: list[ClientInvoice], ref: str) -> list[ClientInvoice]:
    """By number first, else by client name; an exact match beats a partial one."""
    key = _key(ref).lstrip("#").strip()
    key = re.sub(r"^(?:n[o°º]?\.?|nr\.?|no\.?|numero|number|nummer)\s*", "", key)
    by_number = [r for r in rows if r.number and _key(r.number) == key]
    if by_number:
        return by_number
    exact = [r for r in rows if _key(r.client) == key]
    if exact:
        return exact
    return [r for r in rows if key and key in _key(r.client)]


def _label(inv: ClientInvoice) -> str:
    return f"{inv.client} (#{inv.number})" if inv.number else inv.client


def _status_line(inv: ClientInvoice, today: date, lang: str) -> str:
    when = f"{inv.due_on:%d/%m}"
    tail = _t("inv_overdue_on" if inv.due_on < today else "inv_due_on", lang, date=when)
    return f"• {_label(inv)}: {_fmt(inv.amount)} · {tail}"


async def open_totals(session: AsyncSession, member_id: uuid.UUID, today: date) -> dict:
    """Open and overdue amounts for the summary and the panel."""
    rows = (
        await session.execute(
            select(ClientInvoice.amount, ClientInvoice.due_on).where(
                ClientInvoice.member_id == member_id, ClientInvoice.paid_on.is_(None)
            )
        )
    ).all()
    open_total = sum((Decimal(str(a)) for a, _ in rows), Decimal(0))
    late = [Decimal(str(a)) for a, d in rows if d < today]
    return {
        "open": open_total,
        "count": len(rows),
        "overdue": sum(late, Decimal(0)),
        "overdue_count": len(late),
    }


async def handle_invoice_command(
    raw: str, body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is clearly about client invoices (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 160 or not _QUICK.search(plain) and plain not in _LIST:
        return None
    raw_norm = unicodedata.normalize("NFC", " ".join(raw.split()).strip(" .!?"))

    if plain in _LIST:
        return await _list(member, lang, session, today)
    if m := _CREATE.match(plain):
        return await _create(m, raw_norm, plain, member, lang, session, today)
    for pat in (_PAID_A, _PAID_B, _PAID_C):
        if m := pat.match(plain):
            return await _pay(m.group("ref"), member, lang, session, today)
    if m := _DELETE.match(plain):
        return await _remove(m.group("ref"), member, lang, session)
    return None


async def _create(
    m: re.Match,
    raw_norm: str,
    plain: str,
    member: Member,
    lang: str,
    session: AsyncSession,
    today: date,
) -> Reply:
    amount = to_amount(m.group("amt"))
    if amount is None or not (0 < amount <= MAX_AMOUNT):
        return Reply(_t("inv_bad_amount", lang))
    btw = int(m.group("btw")) if m.group("btw") else None
    if btw is not None and btw not in RATES:
        return Reply(_t("inv_bad_btw", lang))
    client = _client(raw_norm, plain, m.span("client"))
    if client is None:
        return Reply(_t("inv_bad_amount", lang))
    due = parse_due(m.group("due"), today)
    if due is None:
        return Reply(_t("inv_bad_due", lang))
    number = (m.group("num") or m.group("num2") or "").upper() or None
    rows = await _all(session, member)
    if sum(1 for r in rows if r.paid_on is None) >= MAX_OPEN:
        return Reply(_t("inv_limit", lang, n=MAX_OPEN))
    if number and any(r.number == number for r in rows):
        return Reply(_t("inv_number_taken", lang, number=number))
    inv = ClientInvoice(
        id=uuid.uuid4(),
        member_id=member.id,
        client=client,
        number=number,
        amount=round(amount, 2),
        btw_rate=btw,
        issued_on=today,
        due_on=due,
    )
    session.add(inv)
    await session.flush()
    audit(session, "invoice_created", member.id)
    text = _t(
        "inv_created",
        lang,
        who=_label(inv),
        amount=_fmt(inv.amount),
        due=f"{due:%d/%m/%Y}",
        ref=number or client,
    )
    if btw is None:
        text += _t("inv_no_btw", lang)
    return Reply(text, [(f"inv_undo:{inv.id}", _t("btn_inv_undo", lang))])


async def _one(
    ref: str, member: Member, lang: str, session: AsyncSession, *, open_only: bool
) -> tuple[ClientInvoice | None, Reply | None]:
    rows = await _all(session, member)
    if open_only:
        pool = [r for r in rows if r.paid_on is None]
        found = _match(pool, ref)
        if not found and _match(rows, ref):
            return None, Reply(_t("inv_already_paid", lang))
    else:
        found = _match(rows, ref)
    if not found:
        return None, Reply(_t("inv_not_found", lang))
    if len(found) > 1:
        names = ", ".join(_label(r) for r in found[:5])
        return None, Reply(_t("inv_ambiguous", lang, names=names))
    return found[0], None


async def _pay(ref: str, member: Member, lang: str, session: AsyncSession, today: date) -> Reply:
    inv, problem = await _one(ref, member, lang, session, open_only=True)
    if inv is None:
        return problem or Reply(_t("inv_not_found", lang))
    income = Expense(
        id=uuid.uuid4(),
        member_id=member.id,
        household_id=member.household_id,
        transaction_type="income",
        amount=inv.amount,
        currency="EUR",
        merchant=inv.client,
        category="inkomen",
        description=(f"Invoice {inv.number}" if inv.number else "Invoice")[:60],
        expense_date=now_local(),
        status="received",
        scope="business",
        btw_rate=inv.btw_rate,
    )
    session.add(income)
    await session.flush()
    inv.paid_on = today
    inv.income_id = income.id
    session.add(inv)
    audit(session, "invoice_paid", member.id)
    return Reply(
        _t("inv_paid", lang, who=_label(inv), amount=_fmt(inv.amount)),
        [(f"inv_unpay:{inv.id}", _t("btn_inv_undo", lang))],
    )


async def _remove(ref: str, member: Member, lang: str, session: AsyncSession) -> Reply:
    inv, problem = await _one(ref, member, lang, session, open_only=False)
    if inv is None:
        return problem or Reply(_t("inv_not_found", lang))
    label = _label(inv)
    await session.delete(inv)
    audit(session, "invoice_deleted", member.id)
    return Reply(_t("inv_deleted", lang, who=label))


async def _list(member: Member, lang: str, session: AsyncSession, today: date) -> Reply:
    rows = [r for r in await _all(session, member) if r.paid_on is None]
    if not rows:
        return Reply(_t("inv_list_empty", lang))
    totals = await open_totals(session, member.id, today)
    lines = [_t("inv_list_title", lang) + "\n"]
    lines += [_status_line(r, today, lang) for r in rows[:15]]
    if len(rows) > 15:
        lines.append(_t("inv_list_more", lang, n=len(rows) - 15))
    lines.append("\n" + _t("inv_list_total", lang, amount=_fmt(totals["open"]), n=totals["count"]))
    if totals["overdue_count"]:
        lines.append(
            _t(
                "inv_list_overdue",
                lang,
                amount=_fmt(totals["overdue"]),
                n=totals["overdue_count"],
            )
        )
    return Reply("\n".join(lines))


async def handle_invoice_button(
    action: str, raw_id: str, member: Member, lang: str, session: AsyncSession
) -> str:
    """``inv_undo`` removes a just-registered open invoice; ``inv_unpay`` reopens a paid one."""
    try:
        inv_id = uuid.UUID(raw_id)
    except ValueError:
        return _t("button_gone", lang)
    inv = await session.scalar(
        select(ClientInvoice).where(
            ClientInvoice.id == inv_id, ClientInvoice.member_id == member.id
        )
    )
    if inv is None:
        return _t("button_gone", lang)
    if action == "inv_undo":
        if inv.paid_on is not None:
            return _t("button_gone", lang)
        label = _label(inv)
        await session.delete(inv)
        audit(session, "invoice_deleted", member.id)
        return _t("inv_deleted", lang, who=label)
    if inv.paid_on is None:
        return _t("button_gone", lang)
    if inv.income_id:
        await session.execute(
            delete(Expense).where(Expense.id == inv.income_id, Expense.member_id == member.id)
        )
    inv.paid_on = None
    inv.income_id = None
    session.add(inv)
    audit(session, "invoice_reopened", member.id)
    return _t("inv_reopened", lang, who=_label(inv))


async def count_open(session: AsyncSession, member_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ClientInvoice)
            .where(ClientInvoice.member_id == member_id, ClientInvoice.paid_on.is_(None))
        )
        or 0
    )


def _all5(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str]] = {
    "btn_inv_undo": _all5("Desfazer", "Ongedaan maken", "Undo", "Annuler", "Rückgängig"),
    "inv_created": _all5(
        "Fatura registrada: {who}, {amount}, vence em {due}. Quando receber, escreva *fatura {ref} paga*.",
        "Factuur geregistreerd: {who}, {amount}, vervalt op {due}. Zodra je betaald bent, schrijf je *factuur {ref} betaald*.",
        "Invoice registered: {who}, {amount}, due {due}. When you get paid, write *invoice {ref} paid*.",
        "Facture enregistrée : {who}, {amount}, échéance le {due}. Quand tu es payé, écris *facture {ref} payée*.",
        "Rechnung erfasst: {who}, {amount}, fällig am {due}. Sobald du bezahlt wirst, schreib *rechnung {ref} bezahlt*.",
    ),
    "inv_no_btw": _all5(
        " Sem BTW informado: o valor conta como total. Para informar, escreva por exemplo *fatura para Acme 1210 btw 21*.",
        " Geen btw opgegeven: het bedrag telt als totaal. Voeg bijvoorbeeld *btw 21* toe.",
        " No VAT given: the amount counts as the total. Add for example *btw 21* next time.",
        " TVA non indiquée : le montant compte comme total. Ajoute par exemple *btw 21* la prochaine fois.",
        " Keine MwSt. angegeben: der Betrag gilt als Gesamtbetrag. Ergänze beim nächsten Mal zum Beispiel *btw 21*.",
    ),
    "inv_bad_amount": _all5(
        "Não consegui ler o valor. Exemplo: *fatura nº 2026-014 para Acme 1210 btw 21 vence em 30 dias*.",
        "Ik kon het bedrag niet lezen. Voorbeeld: *factuur nr 2026-014 voor Acme 1210 btw 21 vervalt over 30 dagen*.",
        "I couldn't read the amount. Example: *invoice no 2026-014 to Acme 1210 btw 21 due in 30 days*.",
        "Je n'ai pas pu lire le montant. Exemple : *facture n° 2026-014 pour Acme 1210 btw 21 échéance dans 30 jours*.",
        "Ich konnte den Betrag nicht lesen. Beispiel: *rechnung nr 2026-014 an Acme 1210 btw 21 fällig in 30 tagen*.",
    ),
    "inv_bad_btw": _all5(
        "O BTW pode ser 0, 9 ou 21.",
        "De btw kan 0, 9 of 21 zijn.",
        "VAT can be 0, 9 or 21.",
        "La TVA peut être 0, 9 ou 21.",
        "Die MwSt. kann 0, 9 oder 21 sein.",
    ),
    "inv_bad_due": _all5(
        "Não entendi o vencimento. Use *vence em 30 dias* ou *vence 15/11*. Sem vencimento, uso 30 dias.",
        "Ik begreep de vervaldatum niet. Gebruik *vervalt over 30 dagen* of *vervalt 15/11*. Zonder datum gebruik ik 30 dagen.",
        "I didn't understand the due date. Use *due in 30 days* or *due 15/11*. Without one I use 30 days.",
        "Je n'ai pas compris l'échéance. Utilise *échéance dans 30 jours* ou *échéance 15/11*. Sans échéance, j'utilise 30 jours.",
        "Ich habe das Fälligkeitsdatum nicht verstanden. Nutze *fällig in 30 tagen* oder *fällig 15/11*. Ohne Angabe nehme ich 30 Tage.",
    ),
    "inv_limit": _all5(
        "Você já tem {n} faturas abertas, o máximo. Marque algumas como pagas ou apague antes de registrar outra.",
        "Je hebt al {n} openstaande facturen, het maximum. Markeer er eerst een paar als betaald of verwijder ze.",
        "You already have {n} open invoices, the maximum. Mark some as paid or delete a few first.",
        "Tu as déjà {n} factures ouvertes, le maximum. Marque-en quelques-unes comme payées ou supprime-les d'abord.",
        "Du hast bereits {n} offene Rechnungen, das Maximum. Markiere einige als bezahlt oder lösche sie zuerst.",
    ),
    "inv_number_taken": _all5(
        "Você já tem uma fatura com o número {number}.",
        "Je hebt al een factuur met nummer {number}.",
        "You already have an invoice numbered {number}.",
        "Tu as déjà une facture numéro {number}.",
        "Du hast bereits eine Rechnung mit der Nummer {number}.",
    ),
    "inv_not_found": _all5(
        "Não achei essa fatura. Escreva *faturas* para ver as abertas.",
        "Ik vond die factuur niet. Schrijf *facturen* om de openstaande te zien.",
        "I couldn't find that invoice. Write *invoices* to see the open ones.",
        "Je n'ai pas trouvé cette facture. Écris *factures* pour voir les ouvertes.",
        "Ich habe diese Rechnung nicht gefunden. Schreib *rechnungen*, um die offenen zu sehen.",
    ),
    "inv_ambiguous": _all5(
        "Achei mais de uma: {names}. Diga o número da fatura.",
        "Ik vond er meer dan één: {names}. Geef het factuurnummer.",
        "I found more than one: {names}. Tell me the invoice number.",
        "J'en ai trouvé plusieurs : {names}. Donne le numéro de la facture.",
        "Ich habe mehrere gefunden: {names}. Nenne die Rechnungsnummer.",
    ),
    "inv_already_paid": _all5(
        "Essa fatura já está paga.",
        "Die factuur is al betaald.",
        "That invoice is already paid.",
        "Cette facture est déjà payée.",
        "Diese Rechnung ist schon bezahlt.",
    ),
    "inv_paid": _all5(
        "Fatura de {who} marcada como paga: {amount}. Entrou como receita da empresa.",
        "Factuur van {who} als betaald gemarkeerd: {amount}. Verwerkt als zakelijke inkomsten.",
        "Invoice from {who} marked as paid: {amount}. Booked as business income.",
        "Facture de {who} marquée comme payée : {amount}. Enregistrée comme revenu professionnel.",
        "Rechnung von {who} als bezahlt markiert: {amount}. Als betriebliche Einnahme gebucht.",
    ),
    "inv_reopened": _all5(
        "Fatura de {who} reaberta e a receita removida.",
        "Factuur van {who} heropend en de inkomsten verwijderd.",
        "Invoice from {who} reopened and the income removed.",
        "Facture de {who} rouverte et le revenu supprimé.",
        "Rechnung von {who} wieder geöffnet und die Einnahme entfernt.",
    ),
    "inv_deleted": _all5(
        "Fatura de {who} apagada.",
        "Factuur van {who} verwijderd.",
        "Invoice from {who} deleted.",
        "Facture de {who} supprimée.",
        "Rechnung von {who} gelöscht.",
    ),
    "inv_list_empty": _all5(
        "Você não tem faturas abertas. Para registrar: *fatura para Acme 1210 btw 21 vence em 30 dias*.",
        "Je hebt geen openstaande facturen. Registreren kan zo: *factuur voor Acme 1210 btw 21 vervalt over 30 dagen*.",
        "You have no open invoices. To register one: *invoice to Acme 1210 btw 21 due in 30 days*.",
        "Tu n'as aucune facture ouverte. Pour en enregistrer une : *facture pour Acme 1210 btw 21 échéance dans 30 jours*.",
        "Du hast keine offenen Rechnungen. Zum Erfassen: *rechnung an Acme 1210 btw 21 fällig in 30 tagen*.",
    ),
    "inv_list_title": _all5(
        "Faturas abertas:",
        "Openstaande facturen:",
        "Open invoices:",
        "Factures ouvertes :",
        "Offene Rechnungen:",
    ),
    "inv_due_on": _all5(
        "vence {date}", "vervalt {date}", "due {date}", "échéance {date}", "fällig {date}"
    ),
    "inv_overdue_on": _all5(
        "venceu {date}",
        "vervallen {date}",
        "overdue since {date}",
        "échue depuis {date}",
        "überfällig seit {date}",
    ),
    "inv_list_more": _all5(
        "… e mais {n}.",
        "… en nog {n}.",
        "… and {n} more.",
        "… et {n} de plus.",
        "… und {n} weitere.",
    ),
    "inv_list_total": _all5(
        "Em aberto: {amount} ({n}).",
        "Openstaand: {amount} ({n}).",
        "Outstanding: {amount} ({n}).",
        "En attente : {amount} ({n}).",
        "Offen: {amount} ({n}).",
    ),
    "inv_list_overdue": _all5(
        "Em atraso: {amount} ({n}).",
        "Te laat: {amount} ({n}).",
        "Overdue: {amount} ({n}).",
        "En retard : {amount} ({n}).",
        "Überfällig: {amount} ({n}).",
    ),
    "acct_summary_invoices": _all5(
        "\nA receber de clientes: {open} ({n} faturas){late}",
        "\nTe ontvangen van klanten: {open} ({n} facturen){late}",
        "\nOwed by clients: {open} ({n} invoices){late}",
        "\nÀ recevoir des clients : {open} ({n} factures){late}",
        "\nOffen bei Kunden: {open} ({n} Rechnungen){late}",
    ),
    "acct_summary_invoices_late": _all5(
        ", {late} em atraso",
        ", {late} te laat",
        ", {late} overdue",
        ", dont {late} en retard",
        ", davon {late} überfällig",
    ),
}
