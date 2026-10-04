# ruff: noqa: E501
"""V2-12 — the Accounting tab and its chat commands, for people with and without a business.

Everyone gets the personal analyses: the year's balance by month, where the money went, fixed
against variable spending and how many months an emergency fund would last. A member who switches
to *business mode* (ZZP) also tags entries as business, optionally with the BTW percentage inside
the amount, and gets the business profit, the BTW per quarter, the deductible spending and the tax
reserve, plus a CSV for the accountant. It is a record-keeping aid, not a tax return, and nothing
is assumed: an entry without a BTW percentage is counted as it is and listed as "unrated".

Everything is rules and ``Decimal`` arithmetic (no LLM). The maths are pure functions over
``Line`` tuples so they can be tested without a database; amounts are gross (VAT included), as
people type them.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import day_start, to_local
from alfred.couple import Reply, parse_categories
from alfred.labels import category_label
from alfred.models import SETTLED, Expense, Member
from alfred.panel_calc import FIXED_CATEGORIES, recurring_names
from alfred.statement_csv import parse_amount

PERSONAL, BUSINESS = "personal", "business"
RATES = (0, 9, 21)
MAX_YEAR_ROWS = 20000  # a year of a very busy member; protects memory
_CENT = Decimal("0.01")
_ZERO = Decimal(0)


@dataclass(frozen=True)
class Line:
    day: date
    kind: str  # income | expense
    amount: Decimal  # gross
    category: str | None
    scope: str
    btw: int | None
    deductible: bool
    merchant: str | None = None


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, ROUND_HALF_UP)


def btw_part(gross: Decimal, rate: int) -> Decimal:
    """The VAT contained in a gross amount: ``gross * rate / (100 + rate)``."""
    return _q(gross * rate / (100 + rate))


def quarter(day: date) -> int:
    return (day.month - 1) // 3 + 1


# ── pure maths ────────────────────────────────────────────────────────────────


def year_summary(lines: list[Line], last_month: int) -> dict:
    """Income, spending, balance and savings rate of all entries, plus one row per month."""
    months = [
        {"m": m, "income": _ZERO, "expense": _ZERO, "balance": _ZERO}
        for m in range(1, last_month + 1)
    ]
    income = expense = _ZERO
    for ln in lines:
        if ln.day.month > last_month:
            continue
        row = months[ln.day.month - 1]
        if ln.kind == "income":
            income += ln.amount
            row["income"] += ln.amount
        else:
            expense += ln.amount
            row["expense"] += ln.amount
    for row in months:
        row["balance"] = row["income"] - row["expense"]
    balance = income - expense
    rate = (balance / income * 100).quantize(Decimal("0.1"), ROUND_HALF_UP) if income > 0 else None
    return {
        "income": income,
        "expense": expense,
        "balance": balance,
        "saving_rate": rate,
        "months": months,
    }


def by_category(lines: list[Line]) -> list[tuple[str, Decimal]]:
    totals: dict[str, Decimal] = {}
    for ln in lines:
        if ln.kind == "expense":
            key = ln.category or "overig"
            totals[key] = totals.get(key, _ZERO) + ln.amount
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def fixed_split(lines: list[Line], names: set[str]) -> tuple[Decimal, Decimal]:
    """(fixed, variable) spending: fixed = a known recurring name or a housing/subscription category."""
    fixed = variable = _ZERO
    for ln in lines:
        if ln.kind != "expense":
            continue
        is_fixed = (ln.merchant or "").lower() in names or ln.category in FIXED_CATEGORIES
        if is_fixed:
            fixed += ln.amount
        else:
            variable += ln.amount
    return fixed, variable


def _net(ln: Line) -> Decimal:
    """Amount without VAT when the rate is known, else as typed."""
    return ln.amount - btw_part(ln.amount, ln.btw) if ln.btw is not None else ln.amount


def business_summary(lines: list[Line], last_month: int) -> dict:
    """Business income, deductible spending and profit, all without VAT where the rate is known."""
    months = [
        {"m": m, "income": _ZERO, "expense": _ZERO, "profit": _ZERO}
        for m in range(1, last_month + 1)
    ]
    income = expense = non_deductible = _ZERO
    unrated = 0
    for ln in lines:
        if ln.scope != BUSINESS or ln.day.month > last_month:
            continue
        if ln.kind == "expense" and not ln.deductible:
            non_deductible += ln.amount
            continue
        if ln.btw is None:
            unrated += 1
        row = months[ln.day.month - 1]
        if ln.kind == "income":
            income += _net(ln)
            row["income"] += _net(ln)
        else:
            expense += _net(ln)
            row["expense"] += _net(ln)
    for row in months:
        row["profit"] = row["income"] - row["expense"]
    return {
        "income": income,
        "expense": expense,
        "profit": income - expense,
        "non_deductible": non_deductible,
        "unrated": unrated,
        "months": months,
    }


def btw_quarters(lines: list[Line]) -> list[dict]:
    """BTW per quarter: owed on business income, input BTW on deductible spending, and the net.

    A negative net is a refund. Non-deductible spending carries no recoverable BTW.
    """
    out = [
        {"q": q, "owed": _ZERO, "input": _ZERO, "net": _ZERO, "unrated": 0} for q in (1, 2, 3, 4)
    ]
    for ln in lines:
        if ln.scope != BUSINESS or (ln.kind == "expense" and not ln.deductible):
            continue
        row = out[quarter(ln.day) - 1]
        if ln.btw is None:
            row["unrated"] += 1
            continue
        part = btw_part(ln.amount, ln.btw)
        if ln.kind == "income":
            row["owed"] += part
        else:
            row["input"] += part
    for row in out:
        row["owed"], row["input"] = _q(row["owed"]), _q(row["input"])
        row["net"] = row["owed"] - row["input"]
    return out


def deductible_by_category(lines: list[Line]) -> list[tuple[str, Decimal]]:
    totals: dict[str, Decimal] = {}
    for ln in lines:
        if ln.scope == BUSINESS and ln.kind == "expense" and ln.deductible:
            key = ln.category or "overig"
            totals[key] = totals.get(key, _ZERO) + ln.amount
    return sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))


def reserve(profit: Decimal, pct: int | None) -> Decimal | None:
    """Amount to set aside for taxes at ``pct`` % of a positive profit; None without a percentage."""
    if pct is None:
        return None
    return _q(profit * pct / 100) if profit > 0 else Decimal("0.00")


def months_covered(savings: Decimal | None, avg_monthly: Decimal | None) -> Decimal | None:
    if savings is None or avg_monthly is None or avg_monthly <= 0:
        return None
    return (savings / avg_monthly).quantize(Decimal("0.1"), ROUND_HALF_UP)


def csv_cell(value: object) -> str:
    """A cell that a spreadsheet will not run as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


# ── data ──────────────────────────────────────────────────────────────────────


def _dec(value: float | Decimal | None) -> Decimal:
    return Decimal(str(value or 0)).quantize(_CENT, ROUND_HALF_UP)


def year_window(year: int):
    return day_start(date(year, 1, 1)), day_start(date(year + 1, 1, 1))


async def load_lines(session: AsyncSession, member_id, year: int) -> list[Line]:
    start, end = year_window(year)
    rows = await session.execute(
        select(
            Expense.expense_date,
            Expense.transaction_type,
            Expense.amount,
            Expense.category,
            Expense.scope,
            Expense.btw_rate,
            Expense.deductible,
            Expense.merchant,
        )
        .where(
            Expense.member_id == member_id,
            Expense.status.in_(SETTLED),
            Expense.expense_date >= start,
            Expense.expense_date < end,
        )
        .order_by(Expense.expense_date)
        .limit(MAX_YEAR_ROWS)
    )
    return [
        Line(to_local(d).date(), kind, _dec(amount), cat, scope, btw, ded, merchant)
        for d, kind, amount, cat, scope, btw, ded, merchant in rows.all()
    ]


async def avg_monthly_expense(
    session: AsyncSession, member_id, today: date, months: int = 3
) -> Decimal | None:
    """Average monthly spending over the last full months, or None when there is none."""
    first = today.replace(day=1)
    start = first
    for _ in range(months):
        start = (start - timedelta(days=1)).replace(day=1)
    rows = await session.execute(
        select(Expense.amount).where(
            Expense.member_id == member_id,
            Expense.transaction_type == "expense",
            Expense.status.in_(SETTLED),
            Expense.expense_date >= day_start(start),
            Expense.expense_date < day_start(first),
        )
    )
    total = sum((_dec(a) for (a,) in rows.all()), _ZERO)
    return _q(total / months) if total > 0 else None


def default_scope(member: Member, category: str | None, transaction_type: str = "expense") -> str:
    """Business when the member is in business mode and this category is one they always book so."""
    if (
        member.acct_mode == BUSINESS
        and transaction_type == "expense"
        and category
        and category in (member.business_categories or [])
    ):
        return BUSINESS
    return PERSONAL


# ── panel ─────────────────────────────────────────────────────────────────────


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _hint(key: str, text: str, chat: str | None = None) -> dict[str, object]:
    return {"key": key, "text": text, "chat": chat, "severity": "info"}


async def panel_cards(
    session: AsyncSession, member: Member, lang: str, today: date, year: int | None = None
) -> list[dict[str, object]]:
    year = year or today.year
    year = min(max(year, 2000), today.year)
    last_month = today.month if year == today.year else 12
    lines = await load_lines(session, member.id, year)
    business = member.acct_mode == BUSINESS
    empty = not lines

    ys = year_summary(lines, last_month)
    cards: list[dict[str, object]] = [
        {
            "id": "books_year",
            "empty": empty,
            "values": {
                "year": year,
                "income": _f(ys["income"]),
                "expense": _f(ys["expense"]),
                "balance": _f(ys["balance"]),
                "saving_rate": _f(ys["saving_rate"]),
                "business": business,
            },
            "months": [
                {
                    "m": r["m"],
                    "income": _f(r["income"]),
                    "expense": _f(r["expense"]),
                    "balance": _f(r["balance"]),
                }
                for r in ys["months"]
            ],
            "phrase": None,
        }
    ]
    if empty:
        cards[0]["hint"] = _hint("books_year", _t("books_year_hint", lang))

    cats = by_category(lines)
    total = sum((v for _, v in cats), _ZERO)
    cards.append(
        {
            "id": "books_categories",
            "empty": not cats,
            "values": {"total": _f(total)},
            "items": [
                {
                    "category": c,
                    "label": category_label(c, lang),
                    "amount": _f(v),
                    "pct": round(float(v / total * 100), 1) if total else 0.0,
                }
                for c, v in cats[:8]
            ],
            "phrase": None,
        }
    )
    fixed, variable = fixed_split(lines, await recurring_names(session, member.id))
    cards.append(
        {
            "id": "books_fixed",
            "empty": fixed + variable == 0,
            "values": {
                "fixed": _f(fixed),
                "variable": _f(variable),
                "fixed_pct": round(float(fixed / (fixed + variable) * 100), 1)
                if fixed + variable
                else None,
            },
            "phrase": None,
        }
    )
    savings = None if member.savings_amount is None else _dec(member.savings_amount)
    avg = await avg_monthly_expense(session, member.id, today)
    emergency: dict[str, object] = {
        "id": "books_emergency",
        "empty": savings is None,
        "values": {
            "savings": _f(savings),
            "avg_monthly": _f(avg),
            "months": _f(months_covered(savings, avg)),
        },
        "phrase": None,
    }
    if savings is None:
        emergency["hint"] = _hint(
            "books_emergency", _t("books_emergency_hint", lang), _t("books_emergency_cmd", lang)
        )
    cards.append(emergency)

    if business:
        bs = business_summary(lines, last_month)
        has_business = any(ln.scope == BUSINESS for ln in lines)
        pl: dict[str, object] = {
            "id": "books_pl",
            "empty": not has_business,
            "values": {
                "income": _f(bs["income"]),
                "expense": _f(bs["expense"]),
                "profit": _f(bs["profit"]),
                "non_deductible": _f(bs["non_deductible"]),
                "unrated": bs["unrated"],
            },
            "months": [
                {
                    "m": r["m"],
                    "income": _f(r["income"]),
                    "expense": _f(r["expense"]),
                    "profit": _f(r["profit"]),
                }
                for r in bs["months"]
            ],
            "phrase": None,
        }
        if not has_business:
            pl["hint"] = _hint("books_pl", _t("books_pl_hint", lang), _t("books_pl_cmd", lang))
        cards.append(pl)
        cards.append(
            {
                "id": "books_btw",
                "empty": not has_business,
                "values": {
                    "year": year,
                    "current_quarter": quarter(today) if year == today.year else None,
                },
                "quarters": [
                    {
                        "q": r["q"],
                        "owed": _f(r["owed"]),
                        "input": _f(r["input"]),
                        "net": _f(r["net"]),
                        "unrated": r["unrated"],
                    }
                    for r in btw_quarters(lines)
                ],
                "phrase": None,
            }
        )
        ded = deductible_by_category(lines)
        cards.append(
            {
                "id": "books_deductible",
                "empty": not ded,
                "values": {"total": _f(sum((v for _, v in ded), _ZERO))},
                "items": [
                    {"category": c, "label": category_label(c, lang), "amount": _f(v)}
                    for c, v in ded[:8]
                ],
                "phrase": None,
            }
        )
        res = reserve(bs["profit"], member.tax_reserve_pct)
        reserve_card: dict[str, object] = {
            "id": "books_reserve",
            "empty": member.tax_reserve_pct is None,
            "values": {
                "pct": member.tax_reserve_pct,
                "profit": _f(bs["profit"]),
                "reserve": _f(res),
            },
            "phrase": None,
        }
        if member.tax_reserve_pct is None:
            reserve_card["hint"] = _hint(
                "books_reserve", _t("books_reserve_hint", lang), _t("books_reserve_cmd", lang)
            )
        cards.append(reserve_card)
    return cards


# ── export for the accountant ─────────────────────────────────────────────────

CSV_HEADER = (
    "date", "type", "scope", "merchant", "category", "description", "amount_gross",
    "btw_rate", "btw_amount", "amount_net", "deductible", "status",
)  # fmt: skip


async def export_csv(session: AsyncSession, member: Member, year: int) -> str:
    """The year's entries as CSV: business entries only in business mode, everything otherwise."""
    start, end = year_window(year)
    stmt = select(Expense).where(
        Expense.member_id == member.id,
        Expense.expense_date >= start,
        Expense.expense_date < end,
    )
    if member.acct_mode == BUSINESS:
        stmt = stmt.where(Expense.scope == BUSINESS)
    rows = (
        await session.scalars(stmt.order_by(Expense.expense_date, Expense.id).limit(MAX_YEAR_ROWS))
    ).all()
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for e in rows:
        gross = _dec(e.amount)
        rate = e.btw_rate
        part = btw_part(gross, rate) if rate is not None else None
        w.writerow(
            [
                to_local(e.expense_date).date().isoformat(),
                e.transaction_type,
                e.scope,
                csv_cell(e.merchant),
                csv_cell(e.category),
                csv_cell(e.description),
                f"{gross:.2f}",
                "" if rate is None else rate,
                "" if part is None else f"{part:.2f}",
                f"{gross - part:.2f}" if part is not None else f"{gross:.2f}",
                "yes" if e.deductible else "no",
                e.status,
            ]
        )
    return out.getvalue()


# ── chat ──────────────────────────────────────────────────────────────────────

_MODE_BUSINESS = {
    "modo empresa", "modo zzp", "modo negocio", "zakelijke modus", "modus zzp", "zzp modus",
    "business mode", "zzp mode", "mode entreprise", "mode pro", "mode independant",
    "firmenmodus", "geschaftsmodus", "zzp-modus",
}  # fmt: skip
_MODE_PERSONAL = {
    "modo pessoal", "persoonlijke modus", "personal mode", "mode personnel", "mode perso",
    "privatmodus", "privat modus",
}  # fmt: skip
_BUSINESS = {
    "foi da empresa", "e da empresa", "foi do negocio", "e do negocio", "foi do zzp",
    "was zakelijk", "is zakelijk", "was voor het bedrijf", "business expense",
    "that was business", "it was business", "c'etait pro", "c'etait professionnel",
    "war geschaftlich", "war betrieblich", "ist geschaftlich",
}  # fmt: skip
_PRIVATE = {
    "foi particular", "e particular", "nao foi da empresa", "foi privado", "was prive", "was niet zakelijk",
    "not business", "was not business", "it was private", "c'etait prive", "ce n'etait pas pro",
    "war nicht geschaftlich", "war privat",
}  # fmt: skip
_NOT_DEDUCTIBLE = {
    "nao dedutivel", "nao e dedutivel", "not deductible", "niet aftrekbaar", "non deductible",
    "pas deductible", "nicht absetzbar", "nicht abzugsfahig",
}  # fmt: skip
_DEDUCTIBLE = {
    "dedutivel",
    "e dedutivel",
    "deductible",
    "aftrekbaar",
    "absetzbar",
    "est deductible",
}
_BTW = re.compile(r"^(?:btw|vat|tva|mwst|iva)\s*[:\-]?\s*(?:de |van |von |of )?(\d{1,2})\s*%?$")
_RESERVE = [
    re.compile(
        r"^(?:reserva de impostos?|tax reserve|belastingreserve|reserve d'?impots?|steuerrucklage|steuerreserve)\s*[:\-]?\s*(\d{1,2})\s*%?$"
    ),
    re.compile(r"^separar (\d{1,2})\s*%\s*(?:de |para )?impostos?$"),
    re.compile(r"^set aside (\d{1,2})\s*%\s*(?:for |to )?tax(?:es)?$"),
]
_SAVINGS = re.compile(
    r"^(?:minha reserva de emergencia|reserva de emergencia|meu fundo de emergencia|fundo de emergencia|"
    r"emergency fund|my emergency fund|noodfonds|mijn noodfonds|fonds d'?urgence|mon fonds d'?urgence|"
    r"notgroschen|mein notgroschen)\s*(?:e|eh|is|=|:|de|von|van|est|ist|of)?\s*(?:de |of )?(?:eur|€)?\s*([\d.,]+)\s*(?:euros?|eur|€)?$"
)
_ALWAYS = re.compile(
    r"^(?:sempre da empresa|sempre do negocio|always business|altijd zakelijk|toujours pro|immer geschaftlich)\s*[:\-]?\s*(.+)$"
)
_ALWAYS_CLEAR = {
    "nada sempre da empresa", "nothing always business", "niets altijd zakelijk",
    "rien de toujours pro", "nichts immer geschaftlich",
}  # fmt: skip
_SUMMARY = {
    "contabilidade", "resumo contabil", "balanco do ano", "balanco anual", "accounting",
    "accounting summary", "annual summary", "boekhouding", "jaaroverzicht", "comptabilite",
    "bilan annuel", "buchhaltung", "jahresubersicht",
}  # fmt: skip
_EXPORT = re.compile(
    r"^(?:(?:exportar|export|exporteer|exporter|exportiere?)\s+(?:a |minha |my |mijn |ma |die |meine |de |la )?"
    r"(?:contabilidade|accounting|boekhouding|comptabilite|buchhaltung)"
    r"|(?:boekhouding exporteren|buchhaltung exportieren))(?:\s+(\d{4}))?$"
)


def _t(key: str, lang: str, **kw: object) -> str:
    from alfred.conversation import _t as t

    return t(key, lang, **kw)


def _fmt(value: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


async def handle_accounting_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession, today: date
) -> Reply | None:
    """None unless the message is clearly one of the accounting commands (cheap exit first)."""
    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 90:
        return None

    if plain in _MODE_BUSINESS:
        member.acct_mode = BUSINESS
        audit(session, "acct_mode_business", member.id)
        return Reply(_t("acct_mode_business", lang), [])
    if plain in _MODE_PERSONAL:
        member.acct_mode = PERSONAL
        audit(session, "acct_mode_personal", member.id)
        return Reply(_t("acct_mode_personal", lang), [])
    if plain in _BUSINESS:
        return await _tag(member, lang, session, BUSINESS)
    if plain in _PRIVATE:
        return await _tag(member, lang, session, PERSONAL)
    if plain in _NOT_DEDUCTIBLE:
        return await _deductible(member, lang, session, False)
    if plain in _DEDUCTIBLE:
        return await _deductible(member, lang, session, True)
    if m := _BTW.match(plain):
        return await _set_btw(int(m.group(1)), member, lang, session)
    for pat in _RESERVE:
        if m := pat.match(plain):
            return _set_reserve(int(m.group(1)), member, lang, session)
    if m := _SAVINGS.match(plain):
        return _set_savings(m.group(1), member, lang, session)
    if plain in _ALWAYS_CLEAR:
        member.business_categories = None
        return Reply(_t("acct_always_cleared", lang), [])
    if m := _ALWAYS.match(plain):
        return _set_always(parse_categories(m.group(1)), member, lang)
    if plain in _SUMMARY:
        return await _summary(member, lang, session, today)
    if m := _EXPORT.match(plain):
        year = int(m.group(1)) if m.group(1) else today.year
        return await _export_link(member, lang, session, today, year)
    return None


async def _last_entry(session: AsyncSession, member: Member) -> Expense | None:
    return await session.scalar(
        select(Expense)
        .where(Expense.member_id == member.id)
        .order_by(Expense.created_at.desc(), Expense.id)
        .limit(1)
    )


def _name(exp: Expense, lang: str) -> str:
    return exp.merchant or exp.description or category_label(exp.category, lang)


async def _tag(member: Member, lang: str, session: AsyncSession, scope: str) -> Reply:
    if member.acct_mode != BUSINESS:
        return Reply(_t("acct_need_business_mode", lang), [])
    exp = await _last_entry(session, member)
    if exp is None:
        return Reply(_t("acct_no_entry", lang), [])
    exp.scope = scope
    if scope == PERSONAL:
        exp.btw_rate = None
        exp.deductible = True
    await session.flush()
    audit(session, "entry_business" if scope == BUSINESS else "entry_personal", member.id)
    key = "acct_marked_business" if scope == BUSINESS else "acct_marked_private"
    return Reply(_t(key, lang, name=_name(exp, lang), amount=_fmt(_dec(exp.amount))), [])


async def _set_btw(rate: int, member: Member, lang: str, session: AsyncSession) -> Reply:
    if rate not in RATES:
        return Reply(_t("acct_btw_bad_rate", lang), [])
    exp = await _last_entry(session, member)
    if exp is None:
        return Reply(_t("acct_no_entry", lang), [])
    if member.acct_mode != BUSINESS or exp.scope != BUSINESS:
        return Reply(_t("acct_btw_need_business", lang), [])
    exp.btw_rate = rate
    await session.flush()
    part = btw_part(_dec(exp.amount), rate)
    audit(session, "entry_btw", member.id)
    return Reply(_t("acct_btw_set", lang, name=_name(exp, lang), rate=rate, btw=_fmt(part)), [])


async def _deductible(member: Member, lang: str, session: AsyncSession, value: bool) -> Reply:
    exp = await _last_entry(session, member)
    if exp is None:
        return Reply(_t("acct_no_entry", lang), [])
    if member.acct_mode != BUSINESS or exp.scope != BUSINESS or exp.transaction_type != "expense":
        return Reply(_t("acct_btw_need_business", lang), [])
    exp.deductible = value
    await session.flush()
    key = "acct_deduct_set" if value else "acct_nondeduct_set"
    return Reply(_t(key, lang, name=_name(exp, lang)), [])


def _set_reserve(pct: int, member: Member, lang: str, session: AsyncSession) -> Reply:
    if not 0 <= pct <= 60:
        return Reply(_t("acct_reserve_bad", lang), [])
    member.tax_reserve_pct = pct or None
    audit(session, "tax_reserve_set", member.id)
    if pct == 0:
        return Reply(_t("acct_reserve_off", lang), [])
    return Reply(_t("acct_reserve_set", lang, pct=pct), [])


def _set_savings(raw: str, member: Member, lang: str, session: AsyncSession) -> Reply:
    amount = parse_amount(raw)
    if amount is None or amount < 0 or amount > Decimal("100000000"):
        return Reply(_t("acct_savings_bad", lang), [])
    member.savings_amount = float(amount)
    audit(session, "savings_set", member.id)
    return Reply(_t("acct_savings_set", lang, amount=_fmt(amount)), [])


def _set_always(cats: list[str], member: Member, lang: str) -> Reply:
    if member.acct_mode != BUSINESS:
        return Reply(_t("acct_need_business_mode", lang), [])
    if not cats:
        return Reply(_t("acct_always_none", lang), [])
    member.business_categories = cats
    names = ", ".join(category_label(c, lang) for c in cats)
    return Reply(_t("acct_always_set", lang, cats=names), [])


async def _summary(member: Member, lang: str, session: AsyncSession, today: date) -> Reply:
    lines = await load_lines(session, member.id, today.year)
    if not lines:
        return Reply(_t("acct_summary_empty", lang), [])
    ys = year_summary(lines, today.month)
    cats = by_category(lines)[:3]
    text = _t(
        "acct_summary",
        lang,
        year=today.year,
        income=_fmt(ys["income"]),
        expense=_fmt(ys["expense"]),
        balance=_fmt(ys["balance"]),
        cats=", ".join(f"{category_label(c, lang)} {_fmt(v)}" for c, v in cats) or "-",
    )
    if member.acct_mode == BUSINESS:
        bs = business_summary(lines, today.month)
        q = btw_quarters(lines)[quarter(today) - 1]
        res = reserve(bs["profit"], member.tax_reserve_pct)
        text += _t(
            "acct_summary_business",
            lang,
            income=_fmt(bs["income"]),
            expense=_fmt(bs["expense"]),
            profit=_fmt(bs["profit"]),
            quarter=quarter(today),
            btw=_fmt(q["net"]),
        )
        if bs["unrated"]:
            text += _t("acct_summary_unrated", lang, n=bs["unrated"])
        if res is not None:
            text += _t("acct_summary_reserve", lang, pct=member.tax_reserve_pct, reserve=_fmt(res))
    text += _t("acct_summary_tail", lang)
    audit(session, "accounting_summary", member.id)
    return Reply(text, [])


async def _export_link(
    member: Member, lang: str, session: AsyncSession, today: date, year: int
) -> Reply:
    from alfred.panel_tokens import issue_export_token
    from alfred.settings import settings

    base_url = (settings.base_url or "").rstrip("/")
    if not base_url:
        return Reply(_t("dashboard_no_base_url", lang), [])
    if not 2000 <= year <= today.year:
        return Reply(_t("acct_export_bad_year", lang), [])
    token = await issue_export_token(session, member)
    audit(session, "accounting_export_link", member.id, year=year)
    url = f"{base_url}/api/d/{token}/books-export?year={year}"
    key = "acct_export_business" if member.acct_mode == BUSINESS else "acct_export_all"
    return Reply(_t(key, lang, year=year, url=url, minutes=settings.export_token_ttl_minutes), [])


# ── texts ─────────────────────────────────────────────────────────────────────


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "acct_mode_business": _all(
        "Modo empresa ligado. Marque um lançamento com *foi da empresa* e, se quiser, informe o BTW com *btw 21* (ou 9 ou 0). Para a empresa ter uma categoria padrão: *sempre da empresa: software, transporte*. Veja tudo na aba Contabilidade do painel (*meu dashboard*). Para voltar: *modo pessoal*.",
        "Zakelijke modus aan. Markeer een boeking met *was zakelijk* en geef eventueel de btw door met *btw 21* (of 9 of 0). Voor een standaard zakelijke categorie: *altijd zakelijk: software, vervoer*. Alles staat in het tabblad Boekhouding van je dashboard (*mijn dashboard*). Terug: *persoonlijke modus*.",
        "Business mode on. Mark an entry with *it was business* and, if you like, add the VAT with *btw 21* (or 9 or 0). For a default business category: *always business: software, transport*. Everything is in the Accounting tab of your panel (*my dashboard*). To go back: *personal mode*.",
        "Mode entreprise activé. Marque une écriture avec *c'était pro* et, si tu veux, indique la TVA avec *btw 21* (ou 9 ou 0). Pour une catégorie pro par défaut : *toujours pro : logiciel, transport*. Tout est dans l'onglet Comptabilité du tableau de bord (*mon dashboard*). Pour revenir : *mode personnel*.",
        "Firmenmodus an. Markiere eine Buchung mit *war geschäftlich* und gib bei Bedarf die MwSt mit *btw 21* (oder 9 oder 0) an. Für eine geschäftliche Standardkategorie: *immer geschäftlich: Software, Transport*. Alles steht im Tab Buchhaltung deines Dashboards (*mein Dashboard*). Zurück: *privatmodus*.",
    ),
    "acct_mode_personal": _all(
        "Modo pessoal. Suas marcações da empresa continuam guardadas; a aba Contabilidade volta a mostrar só as análises pessoais.",
        "Persoonlijke modus. Je zakelijke markeringen blijven bewaard; het tabblad Boekhouding toont weer alleen de persoonlijke analyses.",
        "Personal mode. Your business tags stay saved; the Accounting tab shows only the personal analyses again.",
        "Mode personnel. Tes marques pro restent enregistrées ; l'onglet Comptabilité n'affiche plus que les analyses personnelles.",
        "Privatmodus. Deine geschäftlichen Markierungen bleiben gespeichert; der Tab Buchhaltung zeigt wieder nur die privaten Analysen.",
    ),
    "acct_need_business_mode": _all(
        "Isso é do modo empresa. Ligue com *modo empresa* primeiro.",
        "Dit hoort bij de zakelijke modus. Zet hem eerst aan met *zakelijke modus*.",
        "That belongs to business mode. Turn it on first with *business mode*.",
        "Cela relève du mode entreprise. Active-le d'abord avec *mode entreprise*.",
        "Das gehört zum Firmenmodus. Schalte ihn zuerst mit *firmenmodus* ein.",
    ),
    "acct_no_entry": _all(
        "Não achei um lançamento para marcar.",
        "Ik vond geen boeking om te markeren.",
        "I found no entry to mark.",
        "Je n'ai trouvé aucune écriture à marquer.",
        "Ich habe keine Buchung zum Markieren gefunden.",
    ),
    "acct_marked_business": _all(
        "Marcado como da empresa: {name} ({amount}). Se tiver BTW: *btw 21*, *btw 9* ou *btw 0*. Se não for dedutível: *não dedutível*.",
        "Als zakelijk gemarkeerd: {name} ({amount}). Met btw: *btw 21*, *btw 9* of *btw 0*. Niet aftrekbaar: *niet aftrekbaar*.",
        "Marked as business: {name} ({amount}). With VAT: *btw 21*, *btw 9* or *btw 0*. If it is not deductible: *not deductible*.",
        "Marqué comme pro : {name} ({amount}). Avec TVA : *btw 21*, *btw 9* ou *btw 0*. Si non déductible : *non déductible*.",
        "Als geschäftlich markiert: {name} ({amount}). Mit MwSt: *btw 21*, *btw 9* oder *btw 0*. Wenn nicht absetzbar: *nicht absetzbar*.",
    ),
    "acct_marked_private": _all(
        "Voltou a ser pessoal: {name} ({amount}).",
        "Weer persoonlijk: {name} ({amount}).",
        "Back to personal: {name} ({amount}).",
        "De nouveau personnel : {name} ({amount}).",
        "Wieder privat: {name} ({amount}).",
    ),
    "acct_btw_set": _all(
        "BTW {rate}% em {name}: cerca de {btw} do valor é BTW.",
        "Btw {rate}% bij {name}: ongeveer {btw} van het bedrag is btw.",
        "VAT {rate}% on {name}: about {btw} of the amount is VAT.",
        "TVA {rate}% sur {name} : environ {btw} du montant est de la TVA.",
        "MwSt {rate}% bei {name}: etwa {btw} des Betrags sind MwSt.",
    ),
    "acct_btw_need_business": _all(
        "O último lançamento precisa estar marcado como da empresa (*foi da empresa*) no modo empresa.",
        "De laatste boeking moet als zakelijk zijn gemarkeerd (*was zakelijk*) in de zakelijke modus.",
        "The last entry must be marked as business (*it was business*) in business mode.",
        "La dernière écriture doit être marquée pro (*c'était pro*) en mode entreprise.",
        "Die letzte Buchung muss im Firmenmodus als geschäftlich markiert sein (*war geschäftlich*).",
    ),
    "acct_btw_bad_rate": _all(
        "Use 21, 9 ou 0, por exemplo *btw 21*.",
        "Gebruik 21, 9 of 0, bijvoorbeeld *btw 21*.",
        "Use 21, 9 or 0, for example *btw 21*.",
        "Utilise 21, 9 ou 0, par exemple *btw 21*.",
        "Nimm 21, 9 oder 0, zum Beispiel *btw 21*.",
    ),
    "acct_deduct_set": _all(
        "{name} conta como despesa dedutível.",
        "{name} telt als aftrekbare uitgave.",
        "{name} counts as a deductible expense.",
        "{name} compte comme dépense déductible.",
        "{name} zählt als absetzbare Ausgabe.",
    ),
    "acct_nondeduct_set": _all(
        "{name} não conta como dedutível: fica fora do lucro e do BTW da empresa.",
        "{name} telt niet als aftrekbaar: het blijft buiten de winst en de btw van het bedrijf.",
        "{name} no longer counts as deductible: it stays out of the business profit and BTW.",
        "{name} ne compte plus comme déductible : hors du bénéfice et de la TVA de l'entreprise.",
        "{name} zählt nicht mehr als absetzbar: es bleibt außerhalb von Gewinn und MwSt des Betriebs.",
    ),
    "acct_reserve_set": _all(
        "Combinado: separar {pct}% do lucro da empresa para impostos. É só uma estimativa para você se organizar; o valor real vem do seu contador ou da Belastingdienst.",
        "Afgesproken: {pct}% van de winst opzij voor belastingen. Dit is alleen een schatting om je te organiseren; het echte bedrag komt van je boekhouder of de Belastingdienst.",
        "Done: set aside {pct}% of the business profit for taxes. This is only an estimate to help you plan; the real amount comes from your accountant or the Belastingdienst.",
        "C'est noté : mettre {pct}% du bénéfice de côté pour les impôts. Ce n'est qu'une estimation pour t'organiser ; le montant réel vient de ton comptable ou de la Belastingdienst.",
        "Abgemacht: {pct}% des Gewinns für Steuern zurücklegen. Das ist nur eine Schätzung zur Orientierung; den echten Betrag nennt dein Steuerberater oder die Belastingdienst.",
    ),
    "acct_reserve_off": _all(
        "Reserva de imposto desligada.",
        "Belastingreserve uitgezet.",
        "Tax reserve turned off.",
        "Réserve d'impôt désactivée.",
        "Steuerrücklage ausgeschaltet.",
    ),
    "acct_reserve_bad": _all(
        "Use uma porcentagem de 1 a 60, por exemplo *reserva de imposto 30%*.",
        "Gebruik een percentage van 1 tot 60, bijvoorbeeld *belastingreserve 30%*.",
        "Use a percentage from 1 to 60, for example *tax reserve 30%*.",
        "Utilise un pourcentage de 1 à 60, par exemple *réserve d'impôts 30%*.",
        "Nimm einen Prozentsatz von 1 bis 60, zum Beispiel *steuerrücklage 30%*.",
    ),
    "acct_savings_set": _all(
        "Anotado: reserva de emergência de {amount}. Mostro quantos meses ela cobre na aba Contabilidade.",
        "Genoteerd: noodfonds van {amount}. Het tabblad Boekhouding toont hoeveel maanden dat dekt.",
        "Noted: emergency fund of {amount}. The Accounting tab shows how many months it covers.",
        "Noté : fonds d'urgence de {amount}. L'onglet Comptabilité montre combien de mois il couvre.",
        "Notiert: Notgroschen von {amount}. Der Tab Buchhaltung zeigt, wie viele Monate er abdeckt.",
    ),
    "acct_savings_bad": _all(
        "Não entendi o valor. Exemplo: *reserva de emergência 5000*.",
        "Ik begrijp het bedrag niet. Voorbeeld: *noodfonds 5000*.",
        "I did not understand the amount. Example: *emergency fund 5000*.",
        "Je n'ai pas compris le montant. Exemple : *fonds d'urgence 5000*.",
        "Ich habe den Betrag nicht verstanden. Beispiel: *notgroschen 5000*.",
    ),
    "acct_always_set": _all(
        "Combinado: {cats} entram como da empresa sempre que você lançar. Para desfazer: *nada sempre da empresa*.",
        "Afgesproken: {cats} worden zakelijk geboekt als je iets invoert. Ongedaan maken: *niets altijd zakelijk*.",
        "Done: {cats} are booked as business whenever you add something. To undo: *nothing always business*.",
        "C'est noté : {cats} sont enregistrées en pro à chaque saisie. Pour annuler : *rien de toujours pro*.",
        "Abgemacht: {cats} werden bei jeder Eingabe als geschäftlich gebucht. Zum Rückgängigmachen: *nichts immer geschäftlich*.",
    ),
    "acct_always_none": _all(
        "Não reconheci essas categorias. Exemplo: *sempre da empresa: software, transporte*.",
        "Ik herken die categorieën niet. Voorbeeld: *altijd zakelijk: software, vervoer*.",
        "I did not recognise those categories. Example: *always business: software, transport*.",
        "Je n'ai pas reconnu ces catégories. Exemple : *toujours pro : logiciel, transport*.",
        "Ich habe diese Kategorien nicht erkannt. Beispiel: *immer geschäftlich: Software, Transport*.",
    ),
    "acct_always_cleared": _all(
        "Pronto: nenhuma categoria é da empresa por padrão.",
        "Klaar: geen enkele categorie is standaard zakelijk.",
        "Done: no category is business by default.",
        "C'est fait : aucune catégorie n'est pro par défaut.",
        "Fertig: keine Kategorie ist standardmäßig geschäftlich.",
    ),
    "acct_summary_empty": _all(
        "Ainda não há lançamentos neste ano para a contabilidade.",
        "Er zijn dit jaar nog geen boekingen voor de boekhouding.",
        "There are no entries for the accounting yet this year.",
        "Il n'y a pas encore d'écritures cette année pour la comptabilité.",
        "In diesem Jahr gibt es noch keine Buchungen für die Buchhaltung.",
    ),
    "acct_summary": _all(
        "Contabilidade {year}\nEntradas {income} · Saídas {expense} · Saldo {balance}\nMaiores categorias: {cats}",
        "Boekhouding {year}\nInkomsten {income} · Uitgaven {expense} · Saldo {balance}\nGrootste categorieën: {cats}",
        "Accounting {year}\nIncome {income} · Spending {expense} · Balance {balance}\nBiggest categories: {cats}",
        "Comptabilité {year}\nRevenus {income} · Dépenses {expense} · Solde {balance}\nPrincipales catégories : {cats}",
        "Buchhaltung {year}\nEinnahmen {income} · Ausgaben {expense} · Saldo {balance}\nGrößte Kategorien: {cats}",
    ),
    "acct_summary_business": _all(
        "\n\nEmpresa (sem BTW): receita {income}, despesas dedutíveis {expense}, lucro {profit}\nBTW do {quarter}º trimestre: {btw} (positivo = a pagar)",
        "\n\nBedrijf (excl. btw): omzet {income}, aftrekbare kosten {expense}, winst {profit}\nBtw {quarter}e kwartaal: {btw} (positief = te betalen)",
        "\n\nBusiness (excl. VAT): income {income}, deductible costs {expense}, profit {profit}\nBTW for quarter {quarter}: {btw} (positive = to pay)",
        "\n\nEntreprise (hors TVA) : revenus {income}, charges déductibles {expense}, bénéfice {profit}\nTVA du trimestre {quarter} : {btw} (positif = à payer)",
        "\n\nBetrieb (ohne MwSt): Einnahmen {income}, absetzbare Kosten {expense}, Gewinn {profit}\nMwSt Quartal {quarter}: {btw} (positiv = zu zahlen)",
    ),
    "acct_summary_unrated": _all(
        "\nSem BTW informado: {n} lançamentos da empresa, contados pelo valor total.",
        "\nZonder btw-percentage: {n} zakelijke boekingen, voor het volle bedrag meegeteld.",
        "\nWithout a BTW rate: {n} business entries, counted at the full amount.",
        "\nSans taux de TVA : {n} écritures pro, comptées au montant total.",
        "\nOhne MwSt-Satz: {n} geschäftliche Buchungen, mit vollem Betrag gezählt.",
    ),
    "acct_summary_reserve": _all(
        "\nReserva de imposto ({pct}%): {reserve}",
        "\nBelastingreserve ({pct}%): {reserve}",
        "\nTax reserve ({pct}%): {reserve}",
        "\nRéserve d'impôt ({pct}%) : {reserve}",
        "\nSteuerrücklage ({pct}%): {reserve}",
    ),
    "acct_summary_tail": _all(
        "\n\nÉ uma estimativa para organizar suas contas, não uma declaração. Detalhes na aba Contabilidade (*meu dashboard*).",
        "\n\nDit is een schatting om je administratie te ordenen, geen aangifte. Details in het tabblad Boekhouding (*mijn dashboard*).",
        "\n\nThis is an estimate to organise your records, not a tax return. Details in the Accounting tab (*my dashboard*).",
        "\n\nC'est une estimation pour organiser tes comptes, pas une déclaration. Détails dans l'onglet Comptabilité (*mon dashboard*).",
        "\n\nDas ist eine Schätzung zur Ordnung deiner Unterlagen, keine Steuererklärung. Details im Tab Buchhaltung (*mein Dashboard*).",
    ),
    "acct_export_bad_year": _all(
        "Não tenho esse ano. Exemplo: *exportar contabilidade 2026*.",
        "Dat jaar heb ik niet. Voorbeeld: *exporteer boekhouding 2026*.",
        "I do not have that year. Example: *export accounting 2026*.",
        "Je n'ai pas cette année. Exemple : *exporter comptabilité 2026*.",
        "Dieses Jahr habe ich nicht. Beispiel: *buchhaltung exportieren 2026*.",
    ),
    "acct_export_business": _all(
        "CSV de {year} só com os lançamentos da empresa, para o seu contador: {url}\nO link vale uma vez e por {minutes} minutos.",
        "CSV van {year} alleen met de zakelijke boekingen, voor je boekhouder: {url}\nDe link werkt één keer en {minutes} minuten.",
        "CSV for {year} with the business entries only, for your accountant: {url}\nThe link works once and for {minutes} minutes.",
        "CSV de {year} avec les écritures pro seulement, pour ton comptable : {url}\nLe lien est valable une fois et {minutes} minutes.",
        "CSV für {year} nur mit den geschäftlichen Buchungen, für deinen Steuerberater: {url}\nDer Link gilt einmal und {minutes} Minuten.",
    ),
    "acct_export_all": _all(
        "CSV de {year} com todos os seus lançamentos: {url}\nO link vale uma vez e por {minutes} minutos.",
        "CSV van {year} met al je boekingen: {url}\nDe link werkt één keer en {minutes} minuten.",
        "CSV for {year} with all your entries: {url}\nThe link works once and for {minutes} minutes.",
        "CSV de {year} avec toutes tes écritures : {url}\nLe lien est valable une fois et {minutes} minutes.",
        "CSV für {year} mit allen deinen Buchungen: {url}\nDer Link gilt einmal und {minutes} Minuten.",
    ),
    "books_year_hint": _all(
        "Ainda não há lançamentos neste ano.",
        "Er zijn dit jaar nog geen boekingen.",
        "No entries this year yet.",
        "Pas encore d'écritures cette année.",
        "In diesem Jahr gibt es noch keine Buchungen.",
    ),
    "books_emergency_hint": _all(
        "Diga quanto você tem guardado e eu mostro quantos meses isso cobre.",
        "Zeg hoeveel je op zij hebt en ik laat zien hoeveel maanden dat dekt.",
        "Tell me how much you have set aside and I show how many months it covers.",
        "Dis-moi combien tu as de côté et je montre combien de mois cela couvre.",
        "Sag mir, wie viel du zurückgelegt hast, und ich zeige, wie viele Monate das abdeckt.",
    ),
    "books_emergency_cmd": _all(
        "reserva de emergência 5000",
        "noodfonds 5000",
        "emergency fund 5000",
        "fonds d'urgence 5000",
        "notgroschen 5000",
    ),
    "books_pl_hint": _all(
        "Marque lançamentos como da empresa para ver receita, despesas e lucro.",
        "Markeer boekingen als zakelijk om omzet, kosten en winst te zien.",
        "Mark entries as business to see income, costs and profit.",
        "Marque des écritures comme pro pour voir revenus, charges et bénéfice.",
        "Markiere Buchungen als geschäftlich, um Einnahmen, Kosten und Gewinn zu sehen.",
    ),
    "books_pl_cmd": _all(
        "foi da empresa", "was zakelijk", "it was business", "c'était pro", "war geschäftlich"
    ),
    "books_reserve_hint": _all(
        "Diga que parte do lucro você quer separar para impostos.",
        "Zeg welk deel van de winst je voor belastingen opzij wilt zetten.",
        "Tell me what share of the profit you want to set aside for taxes.",
        "Dis-moi quelle part du bénéfice tu veux mettre de côté pour les impôts.",
        "Sag mir, welchen Teil des Gewinns du für Steuern zurücklegen möchtest.",
    ),
    "books_reserve_cmd": _all(
        "reserva de imposto 30%",
        "belastingreserve 30%",
        "tax reserve 30%",
        "réserve d'impôts 30%",
        "steuerrücklage 30%",
    ),
}
