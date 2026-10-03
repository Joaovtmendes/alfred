"""Numbers behind the "Resumo" and "Dinheiro" cards (spec §2, §3). No text, no phrases.

Two layers:

* **pure functions** (``prev_period``, ``project_month``, ``budget_row``, ``occurrences``,
  ``build_upcoming`` ...) that take plain numbers and dates, so every edge case (month turn, year
  turn, 29 Feb, 31 vs 30 days, zeros, negatives) is a unit test without a database;
* **queries**, one ``GROUP BY`` per card (no N+1), that all go through
  :func:`alfred.panel_filters.apply_expense_filter`, the one filter function of the panel. A card
  that needs another period than the one in the URL (the previous month, the last 30 days, the next
  30 days) derives it with :func:`derive` and still goes through the same function, so the
  member scope and every other active filter apply to it too.

Money: sums are done by PostgreSQL on ``NUMERIC(12,2)`` and carried as ``Decimal`` until the JSON
edge (``money``), so cents never drift.

Local days: the day of an entry is its day in ``settings.timezone`` (``to_char(timezone(...))``),
the calendar comes from :mod:`alfred.clock` (DST-safe ``day_start``).
"""

from __future__ import annotations

import math
import statistics
import uuid
from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, false, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.clock import local_tz, to_local
from alfred.insights import count_blue
from alfred.models import PENDING, SETTLED, Budget, Expense, Iou, RecurringItem
from alfred.panel_filters import PanelFilter, apply_expense_filter
from alfred.panel_phrases import budget_pct

PAGE_SIZE = 50
UPCOMING_DAYS = 30
UPCOMING_LIMIT = 10
PROJ_WINDOW_DAYS = 30
PROJ_MIN_HISTORY_DAYS = 10
PROJ_MIN_ENTRIES = 5
DAILY_DAY_LIMIT = 62  # longer ranges are folded into ISO weeks so the series stays small
_FAR_PAST = date(2000, 1, 1)
_ZERO = Decimal(0)


# ── small helpers ─────────────────────────────────────────────────────────────


def dec(value) -> Decimal:
    """Exact cents from whatever the driver returned (Decimal, float, int or None)."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def money(value: Decimal | float | None) -> float:
    return float(dec(value))


def pct_of(part: Decimal, whole: Decimal) -> int | None:
    """Whole percent, half up; ``None`` without a base."""
    if whole <= 0:
        return None
    return int((part * 100 / whole).quantize(Decimal(1), ROUND_HALF_UP))


def pct_change(cur: Decimal, prev: Decimal) -> float | None:
    """Change against ``prev`` in percent, one decimal; ``None`` when there is no base."""
    if prev <= 0:
        return None
    return float(((cur - prev) * 100 / prev).quantize(Decimal("0.1"), ROUND_HALF_UP))


def derive(f: PanelFilter, **changes) -> PanelFilter:
    """Same filter with another period (or fewer narrowings): still fed to the single function."""
    return replace(f, **changes)


def _local_day():
    return func.to_char(func.timezone(local_tz().key, Expense.expense_date), "YYYY-MM-DD")


def _settled(stmt):
    return stmt.where(Expense.status.in_(SETTLED))


def is_calendar_month(f: PanelFilter) -> bool:
    last = monthrange(f.start.year, f.start.month)[1]
    return f.start.day == 1 and f.end == f.start + timedelta(days=last)


def projection_block_reason(f: PanelFilter, today: date) -> str | None:
    """Why the month-end projection is not shown for this view (``None`` = it is).

    A projection of a subset (category, kind, state, trip) or of anything but the running calendar
    month would be a number nobody can read, so it is simply not offered there.
    """
    if f.categories or f.kind or f.states or f.trip_id:
        return "filtered"
    if not (is_calendar_month(f) and f.start <= today < f.end):
        return "not_current_month"
    return None


def month_end_of(day: date) -> date:
    return day.replace(day=monthrange(day.year, day.month)[1])


# ── periods ──────────────────────────────────────────────────────────────────


def prev_period(f: PanelFilter, today: date) -> tuple[date, date]:
    """The period "month against month" compares with, ``[start, end)``.

    * A calendar month that is the current one: the same elapsed days of the previous month
      (never half a month against a whole one), clamped to the length of the previous month
      (31 Oct against 30 Sep, 29 Mar against 28 Feb).
    * Any other calendar month: the whole previous month.
    * A free range: the range of the same length right before it.
    """
    if is_calendar_month(f):
        prev_first = (f.start - timedelta(days=1)).replace(day=1)
        prev_len = monthrange(prev_first.year, prev_first.month)[1]
        if f.start <= today < f.end:
            return prev_first, prev_first + timedelta(days=min(today.day, prev_len))
        return prev_first, prev_first + timedelta(days=prev_len)
    length = (f.end - f.start).days
    return f.start - timedelta(days=length), f.start


def prev_label_date(f: PanelFilter) -> date:
    """A day inside the compared period, for ``month_name`` (the previous month of the filter)."""
    return prev_period(f, f.start)[0]


# ── projection of the month end (pure) ───────────────────────────────────────


@dataclass(frozen=True)
class Projection:
    """Month-end estimate by components. ``projected`` is ``None`` when the data is too thin."""

    sufficient: bool
    realized: Decimal
    committed: Decimal  # bills still to pay until the month end (positive number)
    scheduled_in: Decimal  # income already marked "to receive" until the month end
    variable: Decimal | None  # expected variable spending until the month end (positive)
    projected: Decimal | None
    low: Decimal | None
    high: Decimal | None
    days_left: int
    window_days: int
    entries: int
    history_days: int
    month_end: date


def project_month(
    *,
    realized: Decimal,
    committed: Decimal,
    scheduled_in: Decimal,
    daily: dict[date, Decimal],
    entries: int,
    history_days: int,
    today: date,
) -> Projection:
    """realized + known income - known bills - (average daily variable spending x days left).

    The average runs over the last ``PROJ_WINDOW_DAYS`` days, or over the whole history when it is
    shorter (a member with 12 days of data is not averaged over 30). Needs at least
    ``PROJ_MIN_HISTORY_DAYS`` days of history and ``PROJ_MIN_ENTRIES`` variable entries, else only
    the realized part and the known bills are reported and ``sufficient`` is false. Income that was
    not registered is never projected. The band is one standard deviation of the daily spending
    summed over the days left.
    """
    month_end = month_end_of(today)
    days_left = max((month_end - today).days, 0)
    window = max(min(PROJ_WINDOW_DAYS, history_days), 0)
    base = dict(
        realized=realized,
        committed=committed,
        scheduled_in=scheduled_in,
        days_left=days_left,
        window_days=window,
        entries=entries,
        history_days=history_days,
        month_end=month_end,
    )
    if history_days < PROJ_MIN_HISTORY_DAYS or entries < PROJ_MIN_ENTRIES or window <= 0:
        return Projection(
            sufficient=False, variable=None, projected=None, low=None, high=None, **base
        )
    series = [float(max(daily.get(today - timedelta(days=i), _ZERO), _ZERO)) for i in range(window)]
    mean = sum(series) / window
    std = statistics.pstdev(series) if window > 1 else 0.0
    variable = dec(mean * days_left)
    spread = dec(std * math.sqrt(days_left))
    projected = realized + scheduled_in - committed - variable
    return Projection(
        sufficient=True,
        variable=variable,
        projected=projected,
        low=projected - spread,
        high=projected + spread,
        **base,
    )


# ── budgets (pure) ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetUse:
    category: str
    limit: Decimal
    spent: Decimal
    pct: int
    level: int  # 0 | 80 | 100
    crossed_on: date | None  # day the running total reached the limit
    days_to_80: int | None  # at the current pace; only inside the running month


def budget_row(
    category: str,
    limit: Decimal,
    per_day: dict[date, Decimal],
    entries: int,
    *,
    ref_first: date,
    today: date,
) -> BudgetUse:
    spent = sum(per_day.values(), _ZERO)
    pct = budget_pct(spent, limit)
    level = 100 if pct >= 100 else 80 if pct >= 80 else 0
    crossed: date | None = None
    if limit > 0 and spent >= limit:
        running = _ZERO
        for day in sorted(per_day):
            running += per_day[day]
            if running >= limit:
                crossed = day
                break
    days_to_80: int | None = None
    last = month_end_of(ref_first)
    if ref_first <= today <= last and level == 0 and limit > 0:
        elapsed = (today - ref_first).days + 1
        if elapsed >= 5 and entries >= 3 and spent > 0:
            per = spent / elapsed
            need = (limit * Decimal("0.8") - spent) / per
            n = math.ceil(need)
            if 0 < n <= (last - today).days:
                days_to_80 = n
    return BudgetUse(category, limit, spent, pct, level, crossed, days_to_80)


# ── fixed bills (pure) ───────────────────────────────────────────────────────


def _step(day: date, frequency: str, due_day: int | None) -> date:
    from alfred.recurring import advance

    return advance(day, frequency, due_day)


def occurrences(
    *,
    next_due: date,
    frequency: str,
    due_day: int | None,
    today: date,
    until: date,
    end_date: date | None = None,
    remaining: int | None = None,
) -> list[date]:
    """Due dates of one fixed item up to ``until``.

    An overdue item counts once (never extrapolated for monthly and yearly: the member may simply
    not have said "paguei"); weekly items repeat every 7 days. ``remaining`` caps the instalments.
    """
    out: list[date] = []
    cap = remaining if remaining is not None else 64
    day = next_due
    if day < today:
        out.append(day)
        if frequency != "weekly":
            return out[:cap]
        while day < today:
            day += timedelta(days=7)
    while day <= until and len(out) < cap:
        if end_date and day > end_date:
            break
        out.append(day)
        day = _step(day, frequency, due_day)
    return out[:cap]


@dataclass(frozen=True)
class Bill:
    name: str
    amount: Decimal
    due: date
    days: int  # negative = overdue
    source: str  # "recurring" | "expense"
    direction: str  # "pay" | "receive"
    category: str | None
    kind: str | None = None  # recurring kind, when it is one


def build_upcoming(
    items: list[dict],
    pending: list[dict],
    *,
    today: date,
    horizon: date,
) -> list[Bill]:
    """Merge fixed items and pending entries into one list ordered by due date.

    A fixed item whose name matches a pending entry (same name, ignoring case) is the same bill:
    the concrete entry wins, so a bill marked "to pay" is never counted twice.
    """
    bills: list[Bill] = []
    seen = set()
    for p in pending:
        due = p["day"]
        if due > horizon:
            continue
        name = p["name"] or ""
        seen.add(name.strip().lower())
        bills.append(
            Bill(
                name=name,
                amount=p["amount"],
                due=due,
                days=(due - today).days,
                source="expense",
                direction="pay" if p["type"] == "expense" else "receive",
                category=p["category"],
            )
        )
    for it in items:
        if it["name"].strip().lower() in seen:
            continue
        total, paid = it["installments_total"], it["installments_paid"] or 0
        remaining = None
        if total:
            remaining = total - paid
            if remaining <= 0:
                continue
        occ = occurrences(
            next_due=it["next_due_date"],
            frequency=it["frequency"],
            due_day=it["due_day"],
            today=today,
            until=horizon,
            end_date=it["end_date"],
            remaining=remaining,
        )
        for due in occ[:1]:  # the list shows the next one; ``occurrences`` is for the projection
            bills.append(
                Bill(
                    name=it["name"],
                    amount=it["amount"],
                    due=due,
                    days=(due - today).days,
                    source="recurring",
                    direction="pay",
                    category=it["category"],
                    kind=it["kind"],
                )
            )
    bills.sort(key=lambda b: (b.due, b.name.lower()))
    return bills


# ── queries ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Totals:
    income: Decimal
    expense: Decimal
    count: int

    @property
    def balance(self) -> Decimal:
        return self.income - self.expense


async def settled_totals(session: AsyncSession, member_id: uuid.UUID, f: PanelFilter) -> Totals:
    """Income, expense and entries of the settled rows: one aggregate query."""
    stmt = apply_expense_filter(
        _settled(
            select(
                func.coalesce(
                    func.sum(case((Expense.transaction_type == "income", Expense.amount), else_=0)),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case((Expense.transaction_type == "expense", Expense.amount), else_=0)
                    ),
                    0,
                ),
                func.count(),
            )
        ),
        f,
        member_id,
    )
    income, expense, n = (await session.execute(stmt)).one()
    return Totals(dec(income), dec(expense), int(n))


@dataclass(frozen=True)
class CategoryRow:
    category: str
    amount: Decimal
    count: int


async def category_totals(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter
) -> list[CategoryRow]:
    """Settled spending by category, biggest first (one ``GROUP BY``)."""
    cat = func.coalesce(Expense.category, "overig")
    total = func.sum(Expense.amount)
    stmt = apply_expense_filter(
        _settled(select(cat, total, func.count()).where(Expense.transaction_type == "expense")),
        f,
        member_id,
    ).group_by(cat)
    rows = (await session.execute(stmt.order_by(total.desc(), cat))).all()
    return [CategoryRow(c, dec(a), int(n)) for c, a, n in rows]


@dataclass(frozen=True)
class Entry:
    day: date
    label: str | None
    category: str | None
    kind: str
    amount: Decimal
    status: str


async def top_expenses(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, limit: int = 5
) -> list[Entry]:
    stmt = apply_expense_filter(
        _settled(
            select(
                _local_day(),
                Expense.merchant,
                Expense.description,
                Expense.category,
                Expense.transaction_type,
                Expense.amount,
                Expense.status,
            ).where(Expense.transaction_type == "expense")
        ),
        f,
        member_id,
    )
    rows = (
        await session.execute(stmt.order_by(Expense.amount.desc(), Expense.id).limit(limit))
    ).all()
    return [
        Entry(date.fromisoformat(d), m or desc, c, t, dec(a), s) for d, m, desc, c, t, a, s in rows
    ]


async def recurring_names(session: AsyncSession, member_id: uuid.UUID) -> set[str]:
    """Lower-case names of every fixed item: an expense with that merchant is a fixed expense."""
    rows = await session.execute(
        select(func.lower(RecurringItem.name)).where(RecurringItem.member_id == member_id)
    )
    return {n for (n,) in rows.all() if n}


def _is_fixed(names: set[str]):
    return func.lower(Expense.merchant).in_(sorted(names)) if names else false()


async def fixed_variable(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, names: set[str]
) -> tuple[Decimal, Decimal, int]:
    """(fixed, variable, entries) of the settled spending, in one query."""
    fixed = func.coalesce(func.sum(case((_is_fixed(names), Expense.amount), else_=0)), 0)
    total = func.coalesce(func.sum(Expense.amount), 0)
    stmt = apply_expense_filter(
        _settled(select(fixed, total, func.count()).where(Expense.transaction_type == "expense")),
        f,
        member_id,
    )
    fx, tot, n = (await session.execute(stmt)).one()
    fx, tot = dec(fx), dec(tot)
    return fx, tot - fx, int(n)


async def daily_spending(
    session: AsyncSession,
    member_id: uuid.UUID,
    f: PanelFilter,
    *,
    exclude_names: set[str] | None = None,
) -> dict[date, tuple[Decimal, int]]:
    """Settled spending per local day: ``{day: (sum, entries)}`` (one ``GROUP BY``)."""
    day = _local_day()
    stmt = _settled(
        select(day, func.sum(Expense.amount), func.count()).where(
            Expense.transaction_type == "expense"
        )
    )
    if exclude_names:
        stmt = stmt.where(~_is_fixed(exclude_names))
    stmt = apply_expense_filter(stmt, f, member_id).group_by(day)
    return {
        date.fromisoformat(d): (dec(s), int(n)) for d, s, n in (await session.execute(stmt)).all()
    }


async def daily_net(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter
) -> dict[date, Decimal]:
    """Settled income minus expense per local day (for the days in the black)."""
    day = _local_day()
    net = func.sum(
        case((Expense.transaction_type == "income", Expense.amount), else_=-Expense.amount)
    )
    stmt = apply_expense_filter(_settled(select(day, net)), f, member_id).group_by(day)
    return {date.fromisoformat(d): dec(v) for d, v in (await session.execute(stmt)).all()}


def blue_days(net: dict[date, Decimal], f: PanelFilter, today: date):
    """Days in the black over the filter period, through today (or the period's last day)."""
    last = min(today, f.end - timedelta(days=1))
    if not net or last < f.start:
        return None
    return count_blue(net, f.start, last)


async def first_expense_day(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, today: date
) -> date | None:
    """Local day of the oldest settled expense (history length for the projection)."""
    wide = derive(f, start=_FAR_PAST, end=today + timedelta(days=1))
    stmt = apply_expense_filter(
        _settled(
            select(func.min(Expense.expense_date)).where(Expense.transaction_type == "expense")
        ),
        wide,
        member_id,
    )
    first = (await session.execute(stmt)).scalar()
    return to_local(first).date() if first else None


async def budget_usage(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, today: date
) -> tuple[list[BudgetUse], date]:
    """Every budget of the member (narrowed by the category filter) against the reference month.

    The reference month is the month of the last day of the period (so a month view shows that
    month and a free range shows the month where it ends). Spending goes through the single
    filter with the period replaced by that month: one ``GROUP BY category, day``.
    """
    ref_first = (f.end - timedelta(days=1)).replace(day=1)
    ref_end = month_end_of(ref_first) + timedelta(days=1)
    stmt = select(Budget.category, Budget.monthly_limit).where(Budget.member_id == member_id)
    if f.categories:
        stmt = stmt.where(Budget.category.in_(f.categories))
    budgets = {c: dec(lim) for c, lim in (await session.execute(stmt)).all()}
    if not budgets:
        return [], ref_first
    day = _local_day()
    spend = apply_expense_filter(
        _settled(
            select(Expense.category, day, func.sum(Expense.amount), func.count())
            .where(Expense.transaction_type == "expense")
            .where(Expense.category.in_(sorted(budgets)))
        ),
        derive(f, start=ref_first, end=ref_end, categories=()),
        member_id,
    ).group_by(Expense.category, day)
    per: dict[str, dict[date, Decimal]] = {}
    counts: dict[str, int] = {}
    for cat, d, s, n in (await session.execute(spend)).all():
        per.setdefault(cat, {})[date.fromisoformat(d)] = dec(s)
        counts[cat] = counts.get(cat, 0) + int(n)
    rows = [
        budget_row(c, lim, per.get(c, {}), counts.get(c, 0), ref_first=ref_first, today=today)
        for c, lim in budgets.items()
    ]
    rows.sort(key=lambda r: (-r.pct, r.category))
    return rows, ref_first


def _item_matches(category: str | None, f: PanelFilter) -> bool:
    """Category and kind filters for rows that are not ledger entries (fixed items)."""
    if f.kind == "income":
        return False
    if f.states and "to_pay" not in f.states:
        return False
    return not f.categories or (category or "overig") in f.categories


async def upcoming(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, today: date
) -> list[Bill]:
    """Fixed items and pending entries due from now on (and the overdue ones), by due date.

    Forward-looking: the period in the URL does not apply, the other filters do (the pending
    entries go through the single filter with a ``[today - 1 year, today + 30 days]`` period).
    """
    horizon = today + timedelta(days=UPCOMING_DAYS)
    pend_states = tuple(s for s in PENDING if not f.states or s in f.states)
    pending: list[dict] = []
    if pend_states:
        stmt = apply_expense_filter(
            select(
                _local_day(),
                Expense.merchant,
                Expense.description,
                Expense.amount,
                Expense.transaction_type,
                Expense.category,
            ),
            derive(
                f,
                start=today - timedelta(days=365),
                end=horizon + timedelta(days=1),
                states=pend_states,
            ),
            member_id,
        ).order_by(Expense.expense_date, Expense.id)
        for d, m, desc, a, t, c in (await session.execute(stmt.limit(200))).all():
            pending.append(
                {
                    "day": date.fromisoformat(d),
                    "name": m or desc or "",
                    "amount": dec(a),
                    "type": t,
                    "category": c,
                }
            )
    items: list[dict] = []
    if not f.states or "to_pay" in f.states:
        rows = await session.execute(
            select(RecurringItem).where(
                RecurringItem.member_id == member_id, RecurringItem.active.is_(True)
            )
        )
        for it in rows.scalars().all():
            if not _item_matches(it.category, f):
                continue
            items.append(
                {
                    "name": it.name,
                    "amount": dec(it.amount),
                    "category": it.category,
                    "kind": it.kind,
                    "frequency": it.frequency,
                    "due_day": it.due_day,
                    "next_due_date": it.next_due_date,
                    "end_date": it.end_date,
                    "installments_total": it.installments_total,
                    "installments_paid": it.installments_paid,
                }
            )
    return build_upcoming(items, pending, today=today, horizon=horizon)


async def commitments_until(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, today: date, until: date
) -> tuple[Decimal, Decimal]:
    """(bills still to pay, income still to receive) up to ``until``, weekly items repeated."""
    pend_pay = pend_in = _ZERO
    stmt = apply_expense_filter(
        select(Expense.merchant, Expense.description, Expense.amount, Expense.transaction_type),
        derive(
            f,
            start=today - timedelta(days=365),
            end=until + timedelta(days=1),
            states=PENDING,
        ),
        member_id,
    )
    names: set[str] = set()
    for m, desc, a, t in (await session.execute(stmt.limit(500))).all():
        names.add((m or desc or "").strip().lower())
        if t == "expense":
            pend_pay += dec(a)
        else:
            pend_in += dec(a)
    rows = await session.execute(
        select(RecurringItem).where(
            RecurringItem.member_id == member_id, RecurringItem.active.is_(True)
        )
    )
    for it in rows.scalars().all():
        if it.name.strip().lower() in names:
            continue
        remaining = None
        if it.installments_total:
            remaining = it.installments_total - (it.installments_paid or 0)
            if remaining <= 0:
                continue
        n = len(
            occurrences(
                next_due=it.next_due_date,
                frequency=it.frequency,
                due_day=it.due_day,
                today=today,
                until=until,
                end_date=it.end_date,
                remaining=remaining,
            )
        )
        pend_pay += dec(it.amount) * n
    return pend_pay, pend_in


@dataclass(frozen=True)
class Recurring:
    name: str
    amount: Decimal
    kind: str
    frequency: str
    next_due: date
    installment: int | None  # the next instalment's number ("1st of 13")
    installments_total: int | None
    category: str | None


async def recurring_items(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter
) -> list[Recurring]:
    rows = await session.execute(
        select(RecurringItem)
        .where(RecurringItem.member_id == member_id, RecurringItem.active.is_(True))
        .order_by(RecurringItem.next_due_date, RecurringItem.name)
    )
    out: list[Recurring] = []
    for it in rows.scalars().all():
        if not _item_matches(it.category, f):
            continue
        total, paid = it.installments_total, it.installments_paid or 0
        nth = paid + 1 if total and paid < total else None
        out.append(
            Recurring(
                it.name,
                dec(it.amount),
                it.kind,
                it.frequency,
                it.next_due_date,
                nth,
                total,
                it.category,
            )
        )
    return out


@dataclass(frozen=True)
class Owed:
    person: str
    remaining: Decimal
    note: str | None
    since: date
    days: int


async def owed_to_me(
    session: AsyncSession, member_id: uuid.UUID, today: date, limit: int = 5
) -> tuple[list[Owed], Decimal, int]:
    """Open money people owe the member: (oldest first, total, number of open debts).

    Not ledger entries, so the ledger filters do not apply (the person filter is browser-side).
    """
    remaining = Iou.amount - Iou.settled_amount
    base = (
        Iou.member_id == member_id,
        Iou.direction == "owed_to_me",
        Iou.settled_at.is_(None),
        remaining > 0,
    )
    total, n = (
        await session.execute(
            select(func.coalesce(func.sum(remaining), 0), func.count()).where(*base)
        )
    ).one()
    rows = await session.execute(
        select(Iou.person, remaining, Iou.note, Iou.created_at)
        .where(*base)
        .order_by(Iou.created_at, Iou.id)
        .limit(limit)
    )
    out = []
    for person, rem, note, created in rows.all():
        since = to_local(created).date()
        out.append(Owed(person, dec(rem), note, since, max((today - since).days, 0)))
    return out, dec(total), int(n)


# ── transactions list ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DaySum:
    day: date
    income: Decimal
    expense: Decimal
    count: int


async def day_sums(session: AsyncSession, member_id: uuid.UUID, f: PanelFilter) -> list[DaySum]:
    """Per local day: settled income, settled expense and number of entries (all states).

    One ``GROUP BY`` over the whole filtered period, newest day first. The sum of the day is over
    the whole day even when its entries straddle two pages.
    """
    day = _local_day()
    inc = func.coalesce(
        func.sum(
            case(
                (
                    (Expense.transaction_type == "income") & Expense.status.in_(SETTLED),
                    Expense.amount,
                ),
                else_=0,
            )
        ),
        0,
    )
    exp = func.coalesce(
        func.sum(
            case(
                (
                    (Expense.transaction_type == "expense") & Expense.status.in_(SETTLED),
                    Expense.amount,
                ),
                else_=0,
            )
        ),
        0,
    )
    stmt = apply_expense_filter(select(day, inc, exp, func.count()), f, member_id).group_by(day)
    rows = (await session.execute(stmt.order_by(day.desc()))).all()
    return [DaySum(date.fromisoformat(d), dec(i), dec(e), int(n)) for d, i, e, n in rows]


async def transactions_page(
    session: AsyncSession, member_id: uuid.UUID, f: PanelFilter, page: int
) -> list[Entry]:
    """One page (``PAGE_SIZE`` entries) of the filtered list, newest first, stable order."""
    stmt = apply_expense_filter(
        select(
            _local_day(),
            func.coalesce(Expense.merchant, Expense.description, literal("")),
            Expense.category,
            Expense.transaction_type,
            Expense.amount,
            Expense.status,
        ),
        f,
        member_id,
    )
    stmt = stmt.order_by(Expense.expense_date.desc(), Expense.id.desc())
    rows = (await session.execute(stmt.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE))).all()
    return [Entry(date.fromisoformat(d), m or None, c, t, dec(a), s) for d, m, c, t, a, s in rows]


def fold_daily(
    series: dict[date, tuple[Decimal, int]], f: PanelFilter, today: date
) -> tuple[str, list[tuple[date, Decimal]]]:
    """Day-by-day spending over the period (through today), or ISO weeks for long ranges."""
    last = min(today, f.end - timedelta(days=1))
    days = (last - f.start).days + 1
    if days <= 0:
        return "day", []
    if days <= DAILY_DAY_LIMIT:
        return "day", [
            (f.start + timedelta(days=i), series.get(f.start + timedelta(days=i), (_ZERO, 0))[0])
            for i in range(days)
        ]
    weeks: dict[date, Decimal] = {}
    first = f.start - timedelta(days=f.start.weekday())
    d = first
    while d <= last:
        weeks[d] = _ZERO
        d += timedelta(days=7)
    for d, (s, _) in series.items():
        key = d - timedelta(days=d.weekday())
        if key in weeks:
            weeks[key] += s
    return "week", sorted(weeks.items())
