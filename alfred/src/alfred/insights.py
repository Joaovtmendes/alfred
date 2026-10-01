# ruff: noqa: E501
"""V2-15 — two small readings of the month, by rules: days in the black, and month vs month.

**Days in the black** (definition to confirm with J): a day of the current month counts when the
month's running balance (income minus expenses, from day 1 through that day) is zero or more.
Days without any entry carry the balance forward. Also the longest run of such days.

**Month vs month by category**: this month so far against the same days of the previous month
(never half a month against a whole one), with the two biggest rises called out. It reuses the
V2-14 query engine, so the numbers match what an analysis question would say.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.analysis import Spec, run
from alfred.clock import local_tz, today_local
from alfred.labels import category_label
from alfred.models import Expense, Member


def _dec(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _fmt(value: Decimal) -> str:
    from alfred.conversation import _fmt_eur

    return _fmt_eur(float(value))


@dataclass(frozen=True)
class BlueDays:
    blue: int
    elapsed: int
    longest: int
    balance: Decimal


def count_blue(daily_net: dict[date, Decimal], first: date, today: date) -> BlueDays:
    """Pure part: running balance per day from ``first`` through ``today``."""
    balance = Decimal(0)
    blue = streak = longest = elapsed = 0
    day = first
    while day <= today:
        balance += daily_net.get(day, Decimal(0))
        elapsed += 1
        if balance >= 0:
            blue += 1
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0
        day += timedelta(days=1)
    return BlueDays(blue, elapsed, longest, balance)


async def blue_days(session: AsyncSession, member_id, today: date) -> BlueDays | None:
    """None when the month has no entries at all (nothing to measure yet)."""
    first = today.replace(day=1)
    tz = local_tz()
    start = datetime.combine(first, time.min, tzinfo=tz)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=tz)
    day_col = func.to_char(func.timezone(tz.key, Expense.expense_date), "YYYY-MM-DD")
    net = func.sum(
        case((Expense.transaction_type == "income", Expense.amount), else_=-Expense.amount)
    )
    rows = (
        await session.execute(
            select(day_col, net)
            .where(
                Expense.member_id == member_id,
                Expense.expense_date >= start,
                Expense.expense_date < end,
            )
            .group_by(day_col)
        )
    ).all()
    if not rows:
        return None
    return count_blue({date.fromisoformat(d): _dec(v) for d, v in rows}, first, today)


# ── month vs month ────────────────────────────────────────────────────────────

_DAYS = re.compile(
    r"\b(dias? no azul|days? in the black|dagen in de plus|jours? dans le vert|tage im plus)\b"
)
_MOM = re.compile(
    r"(mes contra mes|mes a mes|mes x mes|month over month|month vs month|month versus month|maand tegen maand|"
    r"mois contre mois|monat gegen monat|"
    r"compar\w*.*\b(por categoria|per categorie|by category|par categorie|nach kategorie)\b|"
    r"\b(por categoria|per categorie|by category|par categorie|nach kategorie)\b.*\b(mes passado|vorige maand|last month|mois dernier|letzten monat)\b)"
)


def _delta(cur: Decimal, prev: Decimal) -> str | None:
    if prev == 0:
        return None
    return f"{(cur - prev) / prev * 100:+.0f}%".replace("-", "−")


async def month_vs_month(member: Member, lang: str, session: AsyncSession) -> str:
    from alfred.conversation import _t

    spec = Spec(group_by="category", period="this_month", compare_to="previous_period", top_n=10)
    res = await run(session, member.id, spec, today_local())
    if not res.rows:
        return _t("mom_empty", lang)
    prev = res.prev_by_key or {}
    lines = [
        _t(
            "mom_header",
            lang,
            cur=res.window.label(),
            prev=res.prev_window.label() if res.prev_window else "",
        )
    ]
    for key, value in res.rows:
        before = prev.get(key, Decimal(0))
        d = _delta(value, before)
        tail = f" ({_t('mom_new', lang)})" if before == 0 else (f" ({d})" if d else "")
        lines.append(f"• {category_label(key, lang)}: {_fmt(value)} · {_fmt(before)}{tail}")
    rises = sorted(
        ((v - prev.get(k, Decimal(0)), k) for k, v in res.rows if v - prev.get(k, Decimal(0)) > 0),
        reverse=True,
    )[:2]
    if rises:
        lines.append(
            _t(
                "mom_rises",
                lang,
                items=", ".join(f"{category_label(k, lang)} (+{_fmt(r)})" for r, k in rises),
            )
        )
    return "\n".join(lines)


async def handle_insight_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = " ".join(body_plain.split()).strip(" .!?")
    if len(plain) > 80:
        return None
    if _DAYS.search(plain):
        data = await blue_days(session, member.id, today_local())
        if data is None:
            return _t("blue_none", lang)
        return _t(
            "blue_result",
            lang,
            blue=data.blue,
            elapsed=data.elapsed,
            longest=data.longest,
            balance=_fmt(data.balance),
        )
    if _MOM.search(plain):
        return await month_vs_month(member, lang, session)
    return None


def _all(pt: str, nl: str, en: str, fr: str, de: str) -> dict[str, str | tuple[str, ...]]:
    return {"pt": pt, "nl": nl, "en": en, "fr": fr, "de": de}


STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "blue_result": _all(
        "Este mês: {blue} de {elapsed} dias no azul (saldo acumulado a partir do dia 1 sem ficar negativo). Maior sequência: {longest} dias. Saldo até hoje: {balance}.",
        "Deze maand: {blue} van {elapsed} dagen in de plus (lopend saldo vanaf dag 1 niet negatief). Langste reeks: {longest} dagen. Saldo tot nu: {balance}.",
        "This month: {blue} of {elapsed} days in the black (running balance from day 1 not negative). Longest streak: {longest} days. Balance so far: {balance}.",
        "Ce mois-ci : {blue} jours sur {elapsed} dans le vert (solde cumulé depuis le jour 1 non négatif). Plus longue série : {longest} jours. Solde à ce jour : {balance}.",
        "Diesen Monat: {blue} von {elapsed} Tagen im Plus (laufender Saldo seit Tag 1 nicht negativ). Längste Serie: {longest} Tage. Saldo bisher: {balance}.",
    ),
    "blue_none": _all(
        "Ainda não há lançamentos neste mês para medir os dias no azul.",
        "Er zijn deze maand nog geen boekingen om de dagen in de plus te meten.",
        "There are no entries this month yet to measure days in the black.",
        "Il n'y a pas encore d'opérations ce mois-ci pour mesurer les jours dans le vert.",
        "Diesen Monat gibt es noch keine Buchungen, um die Tage im Plus zu messen.",
    ),
    "mom_header": _all(
        "Este mês ({cur}) contra o mesmo período do mês anterior ({prev}):",
        "Deze maand ({cur}) tegenover dezelfde periode vorige maand ({prev}):",
        "This month ({cur}) against the same days of last month ({prev}):",
        "Ce mois-ci ({cur}) contre la même période du mois dernier ({prev}) :",
        "Diesen Monat ({cur}) im Vergleich zum selben Zeitraum des Vormonats ({prev}):",
    ),
    "mom_new": _all("novo", "nieuw", "new", "nouveau", "neu"),
    "mom_rises": _all(
        "Maiores altas: {items}.",
        "Grootste stijgingen: {items}.",
        "Biggest rises: {items}.",
        "Plus fortes hausses : {items}.",
        "Größte Anstiege: {items}.",
    ),
    "mom_empty": _all(
        "Ainda não há despesas neste mês para comparar.",
        "Er zijn deze maand nog geen uitgaven om te vergelijken.",
        "There are no expenses this month yet to compare.",
        "Il n'y a pas encore de dépenses ce mois-ci à comparer.",
        "Diesen Monat gibt es noch keine Ausgaben zum Vergleichen.",
    ),
}
