"""Hard test 03/10, groups D/E — money panel (defects 13, 14, 15)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from alfred.models import Expense, Iou
from tests.labkit import Lab
from tests.test_panel_cards import _card, _get, _token, db, today  # noqa: F401

TODAY = date(2026, 10, 14)


@db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("a pagar luz 120 dia 20", "wonen"),
        ("a pagar aluguel 900 dia 1", "wonen"),
        ("a pagar internet 40 dia 10", "abonnement"),
        ("a pagar dentista 60 dia 12", "overig"),
    ],
)
async def test_a_bill_gets_its_category_from_its_name(lab: Lab, text: str, category: str):
    """Defect 15: the bill "Luz" was saved as Other."""
    await lab.say(text)
    rows = await lab.rows(select(Expense.category).where(Expense.member_id == lab.member_id))
    assert [r[0] for r in rows] == [category]


@db
@pytest.mark.asyncio
async def test_housing_and_subscriptions_count_as_fixed_without_a_recurring_item(
    lab: Lab,
    client,
    today,  # noqa: F811
):
    """Defect 14: two paid fixed bills showed 0% fixed."""
    today(TODAY)

    def exp(amount, merchant, cat):
        return Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=amount,
            merchant=merchant,
            category=cat,
            status="paid",
            expense_date=datetime(2026, 10, 10, 12, tzinfo=UTC),
        )

    await lab.add(
        exp(100, "Luz", "wonen"),
        exp(50, "Netflix", "abonnement"),
        exp(150, "Jumbo", "supermarkt"),
    )
    fv = _card(await _get(client, await _token(lab.member_id), "money"), "fixed_variable")
    assert fv["values"]["fixed"] == 150.0 and fv["values"]["fixed_pct"] == 50


@db
@pytest.mark.asyncio
async def test_what_i_owe_shows_in_the_owed_card(lab: Lab, client, today):  # noqa: F811
    """Defect 13: "Devo 40 pro Lucas" never reached the panel."""
    today(TODAY)
    await lab.add(
        Iou(
            member_id=lab.member_id,
            person="Lucas",
            amount=40,
            direction="i_owe",
            created_at=datetime(2026, 10, 10, 12, tzinfo=UTC),
        )
    )
    card = _card(await _get(client, await _token(lab.member_id), "money"), "owed")
    assert card["empty"] is False
    assert card["values"] == {"total": 0.0, "count": 0}
    assert card["owing"]["values"] == {"total": 40.0, "count": 1}
    assert [(i["person"], i["amount"]) for i in card["owing"]["items"]] == [("Lucas", 40.0)]


@db
@pytest.mark.asyncio
async def test_entries_start_with_a_capital_whatever_was_typed(lab: Lab, client, today):  # noqa: F811
    """Defect 16: "jumbo" next to "Cinema" in the same list."""
    today(TODAY)

    def exp(amount, merchant):
        return Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=amount,
            merchant=merchant,
            category="overig",
            status="paid",
            expense_date=datetime(2026, 10, 14, 12, tzinfo=UTC),
        )

    await lab.add(exp(20, "jumbo"), exp(10, "café do João"), exp(5, "ALDI"))
    body = await _get(client, await _token(lab.member_id), "money")
    names = {e["merchant"] for d in _card(body, "transactions")["days"] for e in d["entries"]}
    assert names == {"Jumbo", "Café do João", "ALDI"}
    top = {i["merchant"] for i in _card(body, "top_expenses")["items"]}
    assert top == {"Jumbo", "Café do João", "ALDI"}
