from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal, engine
from alfred.models import Expense
from alfred.panel_filters import MAX_RANGE_DAYS, apply_expense_filter, parse_filter

TODAY = date(2026, 10, 2)
db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def test_default_is_the_current_month() -> None:
    f = parse_filter({}, TODAY)
    assert (f.start, f.end) == (date(2026, 10, 1), date(2026, 11, 1))
    assert f.categories == () and f.kind is None and f.states == () and f.trip_id is None


@pytest.mark.parametrize(
    ("params", "start", "end"),
    [
        ({"month": "2026-12"}, date(2026, 12, 1), date(2027, 1, 1)),  # year rollover
        ({"month": "2026-02"}, date(2026, 2, 1), date(2026, 3, 1)),
        ({"month": "2028-02"}, date(2026, 10, 1), date(2026, 11, 1)),  # beyond next year
        ({"month": "2026-13"}, date(2026, 10, 1), date(2026, 11, 1)),
        ({"month": "../etc"}, date(2026, 10, 1), date(2026, 11, 1)),
        ({"from": "2026-09-15", "to": "2026-10-02"}, date(2026, 9, 15), date(2026, 10, 3)),
        ({"from": "2026-10-02", "to": "2026-09-15"}, date(2026, 10, 1), date(2026, 11, 1)),
        ({"from": "2024-01-01", "to": "2026-10-02"}, date(2026, 10, 1), date(2026, 11, 1)),
        ({"from": "2026-02-28", "to": "2026-02-28"}, date(2026, 2, 28), date(2026, 3, 1)),
        ({"from": "2028-02-28", "to": "2028-02-29"}, date(2028, 2, 28), date(2028, 3, 1)),  # leap
        ({"from": "2026-09-15"}, date(2026, 10, 1), date(2026, 11, 1)),  # half a range
    ],
)
def test_period_parsing(params, start, end) -> None:
    f = parse_filter(params, TODAY)
    assert (f.start, f.end) == (start, end)
    assert (f.end - f.start).days <= MAX_RANGE_DAYS


def test_closed_lists_only_and_free_text_is_never_read() -> None:
    f = parse_filter(
        {
            "categories": "supermarkt,restaurants,<script>,x" + "y" * 80,
            "kind": "transfer",
            "state": "to_pay,bogus,paid",
            "merchant": "Albert Heijn",
            "person": "Marta",
            "trip": "not-a-uuid",
        },
        TODAY,
    )
    # the markup item and the 81-character item are dropped, the valid ones kept in order
    assert f.categories == ("supermarkt", "restaurants")
    assert f.kind is None
    assert f.states == ("to_pay", "paid")
    assert f.trip_id is None
    assert not hasattr(f, "merchant") and not hasattr(f, "person")


def test_category_cap_and_dedupe() -> None:
    f = parse_filter({"categories": ",".join(f"c{i}" for i in range(30))}, TODAY)
    assert len(f.categories) == 12
    assert parse_filter({"categories": "a,a,a"}, TODAY).categories == ("a",)


def test_valid_trip_and_kind() -> None:
    t = uuid.uuid4()
    f = parse_filter({"trip": str(t), "kind": "income"}, TODAY)
    assert f.trip_id == t and f.kind == "income"


@db
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"kind": "expense"},
        {"categories": "supermarkt"},
        {"state": "paid"},
        {"kind": "expense", "categories": "supermarkt,restaurants", "state": "paid"},
    ],
)
async def test_card_total_equals_list_total(lab, params) -> None:
    async with AsyncSessionLocal() as s:
        rows = [("supermarkt", "paid"), ("restaurants", "paid"), ("supermarkt", "to_pay")]
        rows.append(("vervoer", "paid"))
        for i, (cat, st) in enumerate(rows):
            s.add(
                Expense(
                    member_id=lab.member_id,
                    household_id=lab.household_id,
                    transaction_type="expense",
                    amount=10.0 * (i + 1),
                    merchant=f"m{i}",
                    category=cat,
                    status=st,
                    expense_date=datetime(2026, 10, 1 + i, 12, tzinfo=UTC),
                )
            )
        await s.commit()
        f = parse_filter(params, TODAY)
        grouped = await s.execute(
            apply_expense_filter(
                select(Expense.category, func.sum(Expense.amount)).group_by(Expense.category),
                f,
                lab.member_id,
            )
        )
        card_total = round(sum(v for _, v in grouped.all()), 2)
        listed = await s.execute(apply_expense_filter(select(Expense.amount), f, lab.member_id))
        list_total = round(sum(a for (a,) in listed.all()), 2)
    assert card_total == list_total
    if not params:
        assert list_total == 100.0
    await engine.dispose()


@db
async def test_filter_is_scoped_to_the_member_and_the_period(lab) -> None:
    async with AsyncSessionLocal() as s:
        for day in (datetime(2026, 9, 30, 12, tzinfo=UTC), datetime(2026, 10, 5, 12, tzinfo=UTC)):
            s.add(
                Expense(
                    member_id=lab.member_id,
                    household_id=lab.household_id,
                    transaction_type="expense",
                    amount=5.0,
                    expense_date=day,
                )
            )
        await s.commit()
        got = await s.execute(
            apply_expense_filter(select(Expense.amount), parse_filter({}, TODAY), lab.member_id)
        )
        assert [a for (a,) in got.all()] == [5.0]  # September is out of the period
        none = await s.execute(
            apply_expense_filter(select(Expense.amount), parse_filter({}, TODAY), uuid.uuid4())
        )
        assert none.all() == []
    await engine.dispose()
