# ruff: noqa: E501
"""Numbers of the Resumo/Dinheiro cards: pure edge cases first, then the queries on a real DB."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from alfred import panel_calc as pc
from alfred.db import AsyncSessionLocal
from alfred.models import Budget, Expense, Iou, RecurringItem
from alfred.panel_filters import PanelFilter, parse_filter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def F(start: date, end: date, **kw) -> PanelFilter:
    return PanelFilter(start=start, end=end, **kw)


OCT = F(date(2026, 10, 1), date(2026, 11, 1))


# ── periods ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flt", "today", "expected"),
    [
        # current month: the same elapsed days of the previous one
        (OCT, date(2026, 10, 14), (date(2026, 9, 1), date(2026, 9, 15))),
        # 31 Oct against a 30-day September: clamped to the whole of September
        (OCT, date(2026, 10, 31), (date(2026, 9, 1), date(2026, 10, 1))),
        # a past month: the whole previous month
        (
            F(date(2026, 8, 1), date(2026, 9, 1)),
            date(2026, 10, 14),
            (date(2026, 7, 1), date(2026, 8, 1)),
        ),
        # year turn
        (
            F(date(2027, 1, 1), date(2027, 2, 1)),
            date(2027, 1, 15),
            (date(2026, 12, 1), date(2026, 12, 16)),
        ),
        # 29 Feb of a leap year against January
        (
            F(date(2028, 2, 1), date(2028, 3, 1)),
            date(2028, 2, 29),
            (date(2028, 1, 1), date(2028, 1, 30)),
        ),
        # 29 Mar against a 28-day February
        (
            F(date(2027, 3, 1), date(2027, 4, 1)),
            date(2027, 3, 29),
            (date(2027, 2, 1), date(2027, 3, 1)),
        ),
        # free range: the same length right before
        (
            F(date(2026, 9, 15), date(2026, 10, 3)),
            date(2026, 10, 2),
            (date(2026, 8, 28), date(2026, 9, 15)),
        ),
    ],
)
def test_prev_period(flt, today, expected) -> None:
    assert pc.prev_period(flt, today) == expected


def test_percent_helpers_have_no_base_for_zero() -> None:
    assert pc.pct_change(D("110"), D("100")) == 10.0
    assert pc.pct_change(D("90"), D("100")) == -10.0
    assert pc.pct_change(D("5"), D("0")) is None
    assert pc.pct_of(D("5"), D("0")) is None
    assert pc.pct_of(D("79.5"), D("100")) == 80  # half up
    assert pc.pct_of(D("123"), D("100")) == 123


# ── projection ───────────────────────────────────────────────────────────────

TODAY = date(2026, 10, 14)


def _daily(per_day: float, days: int = 30) -> dict[date, D]:
    return {TODAY - timedelta(days=i): D(str(per_day)) for i in range(days)}


def _project(**kw):
    base = dict(
        realized=D("1842.30"),
        committed=D("298"),
        scheduled_in=D(0),
        daily=_daily(25),
        entries=20,
        history_days=60,
        today=TODAY,
    )
    return pc.project_month(**{**base, **kw})


def test_projection_by_components() -> None:
    p = _project()
    assert p.sufficient and p.days_left == 17 and p.window_days == 30
    assert p.variable == D("425.00")  # 25 a day x 17 days left
    assert p.projected == D("1842.30") - D("298") - D("425.00")
    assert p.low == p.high == p.projected  # constant spending: no spread


def test_projection_band_grows_with_volatility() -> None:
    daily = {TODAY - timedelta(days=i): D("50" if i % 2 else "0") for i in range(30)}
    p = _project(daily=daily)
    assert p.low < p.projected < p.high
    assert p.high - p.projected == p.projected - p.low


def test_projection_adds_income_marked_to_receive_but_never_invents_income() -> None:
    p = _project(scheduled_in=D("500"))
    assert p.projected == D("1842.30") + 500 - 298 - D("425.00")


@pytest.mark.parametrize(("history", "entries"), [(9, 20), (60, 4), (0, 0)])
def test_projection_needs_ten_days_and_five_entries(history, entries) -> None:
    p = _project(history_days=history, entries=entries)
    assert not p.sufficient
    assert p.projected is None and p.variable is None and p.low is None
    assert p.realized == D("1842.30") and p.committed == D("298")  # still reports the known part


def test_projection_exactly_at_the_thresholds_is_enough() -> None:
    p = _project(history_days=10, entries=5, daily=_daily(10, 10))
    assert p.sufficient and p.window_days == 10  # averaged over 10 days, not 30


def test_projection_short_history_is_not_averaged_over_thirty_days() -> None:
    p = _project(history_days=12, daily=_daily(30, 12))
    assert p.variable == D("510.00")  # 30 a day, not 30 * 12 / 30 = 12 a day


def test_projection_last_day_of_month_has_no_variable_part() -> None:
    p = _project(
        today=date(2026, 10, 31),
        daily={date(2026, 10, 31) - timedelta(days=i): D(9) for i in range(30)},
    )
    assert p.days_left == 0 and p.variable == D("0.00") and p.low == p.high == p.projected


def test_projection_zero_spending_and_negative_realized() -> None:
    p = _project(daily={}, realized=D("-120.50"), committed=D("0"))
    assert p.variable == D("0.00") and p.projected == D("-120.50")


def test_projection_year_end_and_leap_day() -> None:
    p = _project(
        today=date(2026, 12, 20),
        daily={date(2026, 12, 20) - timedelta(days=i): D(10) for i in range(30)},
    )
    assert p.days_left == 11 and p.month_end == date(2026, 12, 31)
    q = _project(
        today=date(2028, 2, 10),
        daily={date(2028, 2, 10) - timedelta(days=i): D(10) for i in range(30)},
    )
    assert q.days_left == 19 and q.month_end == date(2028, 2, 29)


# ── budgets ──────────────────────────────────────────────────────────────────


def _use(limit, per_day, entries=5, today=date(2026, 10, 14), ref=date(2026, 10, 1)):
    return pc.budget_row("restaurant", D(limit), per_day, entries, ref_first=ref, today=today)


def test_budget_levels_at_80_and_100() -> None:
    assert _use(100, {date(2026, 10, 2): D(79)}).level == 0
    assert _use(100, {date(2026, 10, 2): D(80)}).level == 80
    assert _use(100, {date(2026, 10, 2): D(99)}).level == 80
    over = _use(100, {date(2026, 10, 2): D(100)})
    assert over.level == 100 and over.pct == 100
    assert _use(100, {date(2026, 10, 2): D(130)}).pct == 130


def test_budget_remembers_the_day_it_was_crossed() -> None:
    per = {date(2026, 10, 1): D(60), date(2026, 10, 3): D(70), date(2026, 10, 9): D(10)}
    u = _use(120, per)
    assert u.crossed_on == date(2026, 10, 3) and u.spent == D(140)
    assert _use(200, per).crossed_on is None


def test_budget_pace_only_inside_the_running_month_with_enough_data() -> None:
    per = {
        date(2026, 10, 3): D(100),
        date(2026, 10, 9): D(130),
    }  # 230 in 14 days, limit 400 -> 80% at 320
    u = _use(400, per, entries=4)
    assert u.days_to_80 == 6  # 16.43 a day: 90 more takes 6 days
    assert _use(400, per, entries=2).days_to_80 is None  # too few entries
    assert _use(400, per, today=date(2026, 10, 4)).days_to_80 is None  # too early in the month
    assert _use(400, per, today=date(2026, 11, 3)).days_to_80 is None  # past month
    assert _use(0, per).days_to_80 is None and _use(0, per).pct == 0  # no division by zero
    assert _use(400, per, today=date(2026, 10, 14)).level == 0


# ── fixed bills ──────────────────────────────────────────────────────────────


def _occ(**kw):
    base = dict(
        frequency="monthly", due_day=None, today=date(2026, 10, 14), until=date(2026, 10, 31)
    )
    return pc.occurrences(**{**base, **kw})


def test_occurrences_monthly_weekly_yearly() -> None:
    assert _occ(next_due=date(2026, 10, 18)) == [date(2026, 10, 18)]
    assert _occ(next_due=date(2026, 11, 2)) == []
    assert _occ(next_due=date(2026, 10, 15), frequency="weekly") == [
        date(2026, 10, 15),
        date(2026, 10, 22),
        date(2026, 10, 29),
    ]
    assert _occ(next_due=date(2026, 10, 20), frequency="yearly") == [date(2026, 10, 20)]


def test_overdue_counts_once_and_is_not_extrapolated() -> None:
    assert _occ(next_due=date(2026, 9, 25)) == [date(2026, 9, 25)]
    wk = _occ(next_due=date(2026, 9, 24), frequency="weekly")
    assert wk[0] == date(2026, 9, 24) and wk[1:] == [
        date(2026, 10, 15),
        date(2026, 10, 22),
        date(2026, 10, 29),
    ]


def test_occurrences_respect_instalments_and_end_date() -> None:
    assert len(_occ(next_due=date(2026, 10, 15), frequency="weekly", remaining=2)) == 2
    assert _occ(next_due=date(2026, 10, 15), frequency="weekly", end_date=date(2026, 10, 20)) == [
        date(2026, 10, 15)
    ]
    # day 31 clamps in short months
    assert _occ(next_due=date(2026, 10, 31), due_day=31) == [date(2026, 10, 31)]


def _item(name, amount, due, **kw):
    base = dict(
        name=name, amount=D(amount), category="wonen", kind="fixed", frequency="monthly",
        due_day=due.day, next_due_date=due, end_date=None, installments_total=None, installments_paid=0,
    )  # fmt: skip
    return {**base, **kw}


def test_upcoming_merges_and_a_pending_entry_wins_over_its_fixed_item() -> None:
    today = date(2026, 10, 14)
    items = [
        _item("Energia", 118, date(2026, 10, 18)),
        _item("Internet", 42, date(2026, 10, 20)),
        _item("Seguro", 138, date(2026, 12, 25)),  # beyond the horizon: not listed
        _item(
            "Notebook", 100, date(2026, 10, 22), installments_total=3, installments_paid=3
        ),  # finished
    ]
    pending = [
        {
            "day": date(2026, 10, 18),
            "name": "energia",
            "amount": D("120"),
            "type": "expense",
            "category": "wonen",
        }
    ]
    out = pc.build_upcoming(items, pending, today=today, horizon=today + timedelta(days=30))
    assert [(b.name, b.amount, b.source, b.days) for b in out] == [
        ("energia", D("120"), "expense", 4),
        ("Internet", D("42"), "recurring", 6),
    ]


def test_upcoming_flags_overdue_and_receivables() -> None:
    today = date(2026, 10, 14)
    pending = [
        {
            "day": date(2026, 10, 10),
            "name": "Aluguel",
            "amount": D("1150"),
            "type": "expense",
            "category": None,
        },
        {
            "day": date(2026, 10, 20),
            "name": "Cliente",
            "amount": D("300"),
            "type": "income",
            "category": None,
        },
    ]
    out = pc.build_upcoming([], pending, today=today, horizon=today + timedelta(days=30))
    assert [(b.days, b.direction) for b in out] == [(-4, "pay"), (6, "receive")]


# ── queries on a real database ───────────────────────────────────────────────


def _utc(y, m, d, h=12, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=UTC)


async def _add(
    lab, amount, *, day=1, kind="expense", status="paid", cat="overig", merchant=None, at=None
):
    await lab.add(
        Expense(
            member_id=lab.member_id, household_id=lab.household_id, transaction_type=kind,
            amount=amount, merchant=merchant or f"m{uuid.uuid4().hex[:5]}", category=cat, status=status,
            expense_date=at or _utc(2026, 10, day),
        )
    )  # fmt: skip


@db
async def test_entries_are_assigned_to_their_local_day_across_the_month_turn(lab) -> None:
    await _add(lab, 10, at=_utc(2026, 10, 31, 21, 59))  # 23:59 on 31 Oct (CEST, UTC+2)
    await _add(
        lab, 20, at=_utc(2026, 10, 31, 23, 30)
    )  # 00:30 on 1 Nov (the clocks went back on the 25th: CET)
    oct_ = F(date(2026, 10, 1), date(2026, 11, 1))
    nov = F(date(2026, 11, 1), date(2026, 12, 1))
    async with AsyncSessionLocal() as s:
        a = await pc.settled_totals(s, lab.member_id, oct_)
        b = await pc.settled_totals(s, lab.member_id, nov)
        days = await pc.daily_spending(s, lab.member_id, F(date(2026, 10, 25), date(2026, 11, 2)))
    assert a.expense == D(10) and b.expense == D(20)
    assert days == {date(2026, 10, 31): (D(10), 1), date(2026, 11, 1): (D(20), 1)}


@db
async def test_daily_grouping_on_the_dst_days(lab) -> None:
    await _add(lab, 5, at=_utc(2026, 10, 24, 22, 30))  # 00:30 local on 25 Oct (the 25-hour day)
    await _add(lab, 7, at=_utc(2026, 10, 25, 22, 30))  # 23:30 local on 25 Oct (CET)
    await _add(lab, 3, at=_utc(2026, 3, 28, 23, 30))  # 00:30 local on 29 Mar (the 23-hour day)
    async with AsyncSessionLocal() as s:
        oct_ = await pc.daily_spending(s, lab.member_id, F(date(2026, 10, 24), date(2026, 10, 27)))
        mar = await pc.daily_spending(s, lab.member_id, F(date(2026, 3, 28), date(2026, 3, 31)))
    assert oct_ == {date(2026, 10, 25): (D(12), 2)}
    assert mar == {date(2026, 3, 29): (D(3), 1)}


@db
async def test_category_totals_ignore_pending_income_and_other_members(lab) -> None:
    await _add(lab, 100, cat="supermarkt")
    await _add(lab, 40, cat="supermarkt", day=2)
    await _add(lab, 60, cat="restaurant", day=3)
    await _add(lab, 999, cat="restaurant", status="to_pay")  # pending never counts
    await _add(lab, 3000, kind="income", status="received", cat="inkomen")
    async with AsyncSessionLocal() as s:
        rows = await pc.category_totals(s, lab.member_id, OCT)
        stranger = await pc.category_totals(s, uuid.uuid4(), OCT)
    assert [(r.category, r.amount, r.count) for r in rows] == [
        ("supermarkt", D(140), 2),
        ("restaurant", D(60), 1),
    ]
    assert stranger == []


@db
async def test_blue_days_through_a_month_turn(lab) -> None:
    await _add(lab, 100, kind="income", status="received", at=_utc(2026, 10, 30))
    await _add(lab, 150, at=_utc(2026, 10, 31, 10))  # balance -50 on the 31st
    await _add(lab, 100, kind="income", status="received", at=_utc(2026, 11, 2))
    flt = F(date(2026, 10, 25), date(2026, 11, 4))
    async with AsyncSessionLocal() as s:
        net = await pc.daily_net(s, lab.member_id, flt)
    res = pc.blue_days(net, flt, date(2026, 11, 3))
    # 25..29 Oct: 0 (blue), 30: +100, 31: -50, 1 Nov: -50, 2 Nov: +50, 3 Nov: +50
    assert (res.blue, res.elapsed, res.longest) == (8, 10, 6)
    assert pc.blue_days({}, flt, date(2026, 11, 3)) is None
    assert pc.blue_days(net, flt, date(2026, 10, 20)) is None  # the period has not begun


@db
async def test_first_expense_day_and_history(lab) -> None:
    async with AsyncSessionLocal() as s:
        assert await pc.first_expense_day(s, lab.member_id, OCT, date(2026, 10, 14)) is None
    await _add(lab, 5, at=_utc(2026, 9, 20))
    await _add(lab, 5, at=_utc(2026, 10, 3))
    async with AsyncSessionLocal() as s:
        assert await pc.first_expense_day(s, lab.member_id, OCT, date(2026, 10, 14)) == date(
            2026, 9, 20
        )


@db
async def test_fixed_variable_matches_expenses_to_fixed_items_by_name(lab) -> None:
    await lab.add(
        RecurringItem(
            member_id=lab.member_id, household_id=lab.household_id, name="Aluguel", amount=1150,
            next_due_date=date(2026, 11, 1), due_day=1,
        )
    )  # fmt: skip
    await _add(lab, 1150, merchant="aluguel", cat="wonen")
    await _add(lab, 94.40, merchant="Albert Heijn", cat="supermarkt")
    async with AsyncSessionLocal() as s:
        names = await pc.recurring_names(s, lab.member_id)
        fixed, variable, n = await pc.fixed_variable(s, lab.member_id, OCT, names)
        none = await pc.fixed_variable(s, lab.member_id, OCT, set())
    assert (fixed, variable, n) == (D("1150.00"), D("94.40"), 2)
    assert none[0] == D(0) and none[1] == D("1244.40")


@db
async def test_budget_usage_uses_the_reference_month_and_the_category_filter(lab) -> None:
    await lab.add(
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="restaurant", monthly_limit=120),
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="supermarkt", monthly_limit=400),
    )  # fmt: skip
    await _add(lab, 70, cat="restaurant", day=2)
    await _add(lab, 78, cat="restaurant", day=3)
    await _add(lab, 312, cat="supermarkt", day=5)
    await _add(lab, 500, cat="restaurant", at=_utc(2026, 9, 28))  # another month
    async with AsyncSessionLocal() as s:
        rows, ref = await pc.budget_usage(s, lab.member_id, OCT, date(2026, 10, 14))
        only, _ = await pc.budget_usage(
            s, lab.member_id, F(OCT.start, OCT.end, categories=("supermarkt",)), date(2026, 10, 14)
        )
        stranger, _ = await pc.budget_usage(s, uuid.uuid4(), OCT, date(2026, 10, 14))
    assert ref == date(2026, 10, 1) and stranger == []
    assert [(r.category, r.spent, r.pct, r.level) for r in rows] == [
        ("restaurant", D(148), 123, 100),
        ("supermarkt", D(312), 78, 0),
    ]
    assert rows[0].crossed_on == date(2026, 10, 3)
    assert [r.category for r in only] == ["supermarkt"] and only[0].spent == D(312)


@db
async def test_upcoming_and_commitments_share_the_pending_entry_over_the_fixed_item(lab) -> None:
    today = date(2026, 10, 14)
    await lab.add(
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Energia", amount=118,
                      next_due_date=date(2026, 10, 18), due_day=18, category="wonen"),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Internet", amount=42,
                      next_due_date=date(2026, 10, 20), due_day=20, category="wonen"),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Ginásio", amount=20,
                      next_due_date=date(2026, 10, 15), frequency="weekly", category="gezondheid"),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Velho", amount=9,
                      next_due_date=date(2026, 10, 25), due_day=25, active=False),
    )  # fmt: skip
    await _add(lab, 120, status="to_pay", merchant="Energia", cat="wonen", at=_utc(2026, 10, 18))
    await _add(
        lab, 300, kind="income", status="to_receive", merchant="Cliente", at=_utc(2026, 10, 27)
    )
    async with AsyncSessionLocal() as s:
        bills = await pc.upcoming(s, lab.member_id, OCT, today)
        pay, receive = await pc.commitments_until(s, lab.member_id, OCT, today, date(2026, 10, 31))
        cat = await pc.upcoming(
            s, lab.member_id, F(OCT.start, OCT.end, categories=("gezondheid",)), today
        )
        only_paid = await pc.upcoming(
            s, lab.member_id, F(OCT.start, OCT.end, states=("paid",)), today
        )
    assert [(b.name, b.amount, b.source) for b in bills] == [
        ("Ginásio", D(20), "recurring"),
        ("Energia", D(120), "expense"),
        ("Internet", D(42), "recurring"),
        ("Cliente", D(300), "expense"),
    ]
    # commitments: Energia (the entry, 120) + Internet 42 + Ginásio on 15, 22 and 29 (3 x 20)
    assert pay == D(120 + 42 + 60) and receive == D(300)
    assert [b.name for b in cat] == ["Ginásio"] and only_paid == []


@db
async def test_owed_to_me_lists_only_open_debts_of_the_member(lab) -> None:
    other = uuid.uuid4()
    await lab.add(
        Iou(member_id=lab.member_id, person="Marta", amount=34.5, direction="owed_to_me", note="jantar",
            created_at=datetime(2026, 10, 5, 12, tzinfo=UTC)),
        Iou(member_id=lab.member_id, person="Rui", amount=100, settled_amount=60, direction="owed_to_me",
            created_at=datetime(2026, 10, 8, 12, tzinfo=UTC)),
        Iou(member_id=lab.member_id, person="Ana", amount=20, settled_amount=20, direction="owed_to_me",
            settled_at=datetime(2026, 10, 6, tzinfo=UTC)),
        Iou(member_id=lab.member_id, person="Eu devo", amount=50, direction="i_owe"),
    )  # fmt: skip
    async with AsyncSessionLocal() as s:
        rows, total, n = await pc.owed_to_me(s, lab.member_id, date(2026, 10, 14))
        none = await pc.owed_to_me(s, other, date(2026, 10, 14))
    assert (total, n) == (D("74.50"), 2)
    assert [(r.person, r.remaining) for r in rows][0] == ("Marta", D("34.50"))
    assert rows[0].days == 9 and rows[0].note == "jantar"
    assert none == ([], D(0), 0)


# ── one filter, one total (card and list) ────────────────────────────────────

COMBOS = [
    {},
    {"kind": "expense"},
    {"kind": "income"},
    {"categories": "supermarkt"},
    {"categories": "supermarkt,restaurant"},
    {"state": "paid"},
    {"state": "to_pay,paid"},
    {"state": "received,to_receive"},
    {"kind": "expense", "categories": "restaurant", "state": "paid"},
    {"from": "2026-10-02", "to": "2026-10-04"},
    {"from": "2026-09-28", "to": "2026-10-12", "kind": "expense"},
    {"month": "2026-09"},
]


@db
@pytest.mark.parametrize("params", COMBOS, ids=[str(c) for c in COMBOS])
async def test_every_card_and_the_list_agree_on_the_total(lab, params) -> None:
    rows = [
        (100, "expense", "paid", "supermarkt", 2), (45.5, "expense", "paid", "restaurant", 2),
        (30, "expense", "to_pay", "restaurant", 3), (12.25, "expense", "paid", "overig", 4),
        (3000, "income", "received", "inkomen", 1), (250, "income", "to_receive", "inkomen", 5),
        (77.7, "expense", "paid", "supermarkt", 9), (19.99, "expense", "paid", "restaurant", 12),
    ]  # fmt: skip
    for amount, kind, status, cat, day in rows:
        await _add(lab, amount, kind=kind, status=status, cat=cat, day=day)
    await _add(lab, 55.55, cat="supermarkt", at=_utc(2026, 9, 29))
    f = parse_filter(params, date(2026, 10, 14))
    async with AsyncSessionLocal() as s:
        names: set[str] = set()
        totals = await pc.settled_totals(s, lab.member_id, f)
        cats = await pc.category_totals(s, lab.member_id, f)
        fixed, variable, _ = await pc.fixed_variable(s, lab.member_id, f, names)
        daily = await pc.daily_spending(s, lab.member_id, f)
        days = await pc.day_sums(s, lab.member_id, f)
        listed = []
        page = 1
        while True:
            chunk = await pc.transactions_page(s, lab.member_id, f, page)
            listed += chunk
            if len(chunk) < pc.PAGE_SIZE:
                break
            page += 1
        top = await pc.top_expenses(s, lab.member_id, f, limit=1000)
    list_expense = sum(
        (e.amount for e in listed if e.kind == "expense" and e.status in ("paid", "received")), D(0)
    )
    list_income = sum(
        (e.amount for e in listed if e.kind == "income" and e.status in ("paid", "received")), D(0)
    )
    assert totals.expense == sum((c.amount for c in cats), D(0)) == fixed + variable == list_expense
    assert (
        totals.expense
        == sum((v[0] for v in daily.values()), D(0))
        == sum((t.amount for t in top), D(0))
    )
    assert totals.expense == sum((d.expense for d in days), D(0))
    assert totals.income == list_income == sum((d.income for d in days), D(0))
    assert sum(d.count for d in days) == len(listed)


@db
async def test_list_pages_have_fifty_entries_a_stable_order_and_whole_day_sums(lab) -> None:
    for i in range(120):
        await _add(lab, 1 + i % 3, at=_utc(2026, 10, 1 + i % 5, 8 + i % 10, i % 60))
    async with AsyncSessionLocal() as s:
        p1 = await pc.transactions_page(s, lab.member_id, OCT, 1)
        p2 = await pc.transactions_page(s, lab.member_id, OCT, 2)
        p3 = await pc.transactions_page(s, lab.member_id, OCT, 3)
        p4 = await pc.transactions_page(s, lab.member_id, OCT, 4)
        days = await pc.day_sums(s, lab.member_id, OCT)
    assert (len(p1), len(p2), len(p3), len(p4)) == (50, 50, 20, 0)
    keys = [e.day for e in p1 + p2 + p3]
    assert keys == sorted(keys, reverse=True)
    assert sum(d.count for d in days) == 120 and [d.day for d in days] == sorted(
        (d.day for d in days), reverse=True
    )


def test_fold_daily_switches_to_weeks_for_long_ranges() -> None:
    f = F(date(2026, 10, 1), date(2026, 11, 1))
    unit, pts = pc.fold_daily({date(2026, 10, 3): (D(5), 1)}, f, date(2026, 10, 14))
    assert unit == "day" and len(pts) == 14 and pts[2] == (date(2026, 10, 3), D(5))
    long = F(date(2026, 1, 1), date(2026, 11, 1))
    unit, pts = pc.fold_daily(
        {date(2026, 10, 3): (D(5), 1), date(2026, 10, 5): (D(7), 1)}, long, date(2026, 10, 14)
    )
    assert unit == "week" and len(pts) < 50
    assert sum((v for _, v in pts), D(0)) == D(12) and all(d.weekday() == 0 for d, _ in pts)
    assert pc.fold_daily({}, F(date(2026, 11, 1), date(2026, 12, 1)), date(2026, 10, 14)) == (
        "day",
        [],
    )


def test_budget_percent_never_contradicts_its_level() -> None:
    """99.60 of 100 is 99 %, not "100 %, over budget", and 79.50 is not "80 %": the level is
    decided on the exact amounts (as the chat alerts do) and the number stays in its band."""
    near = _use(100, {date(2026, 10, 2): D("99.60")})
    assert near.pct == 99 and near.level == 80 and near.crossed_on is None
    assert _use(100, {date(2026, 10, 2): D("79.50")}).pct == 79
    assert _use(100, {date(2026, 10, 2): D("79.50")}).level == 0
    assert _use(100, {date(2026, 10, 2): D("100")}).level == 100
    assert pc.budget_pct(D("99.99"), D("100")) == 99 and pc.budget_pct(D("5"), D("0")) == 0
