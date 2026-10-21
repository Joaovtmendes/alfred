"""B-079/B-080: unknown budget category offers to create it; the 100 % alert offers a raise."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from alfred.db import AsyncSessionLocal
from alfred.models import Budget
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float) -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt",
            "description": None, "type": "expense", "days_ago": 0}  # fmt: skip


async def _limits(lab: Lab) -> dict[str, float]:
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(Budget).where(Budget.member_id == lab.member_id))).scalars()
        return {b.category: float(b.monthly_limit) for b in rows}


@db
@pytest.mark.bug("B-079")
async def test_unknown_category_is_created_after_a_yes(lab: Lab) -> None:
    ask = await lab.say("orçamento pets 150")
    assert "Pets" in ask and "Quer criar" in ask
    assert await _limits(lab) == {}
    out = await lab.tap(lab.buttons[-1][0][0])
    assert "150" in out
    assert await _limits(lab) == {"pets": 150.0}


@db
@pytest.mark.bug("B-080")
async def test_the_100_percent_alert_offers_buttons_and_raises_by_ten_percent(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.expense.return_value = _exp(130)
    await lab.say("Jumbo 130", replies=2)
    labels = [b[1] for b in lab.buttons[-1]]
    assert labels[:2] == ["+10%", "+20%"]
    await lab.tap(lab.buttons[-1][0][0])
    assert await _limits(lab) == {"supermarkt": 110.0}


@db
@pytest.mark.bug("B-080")
async def test_a_typed_amount_sets_the_limit_but_a_bare_number_is_an_expense(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.expense.return_value = _exp(130)
    await lab.say("Jumbo 130", replies=2)
    lab.expense.return_value = _exp(5)
    await lab.say("Jumbo 5")  # not an answer: the question is dropped, the expense is recorded
    assert await _limits(lab) == {"supermarkt": 100.0}
