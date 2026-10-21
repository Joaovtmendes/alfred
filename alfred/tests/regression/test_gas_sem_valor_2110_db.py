# ruff: noqa: E501
"""B-070 and B-071: "paguei gas" without a price, the amount that answers, and "isso é X"."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from alfred.db import AsyncSessionLocal
from alfred.models import Expense
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str) -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": "overig",
            "description": None, "type": "expense", "days_ago": 0}  # fmt: skip


async def _expenses(lab: Lab) -> list[Expense]:
    rows = await lab.rows(select(Expense).where(Expense.member_id == lab.member_id))
    return [r[0] for r in rows]


async def _age_all(lab: Lab, days: int) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Expense)
            .where(Expense.member_id == lab.member_id)
            .values(created_at=datetime.now(UTC) - timedelta(days=days))
        )
        await s.commit()


@db
@pytest.mark.bug("B-070")
async def test_the_amount_after_a_paid_item_without_price_becomes_that_expense(lab: Lab) -> None:
    out = await lab.say("paguei gas")
    assert "Quanto foi" in out and "gas" in out.lower()
    out = await lab.say("75")
    assert "Não consegui anotar nada" not in out
    rows = await _expenses(lab)
    assert len(rows) == 1 and float(rows[0].amount) == 75.0
    assert (rows[0].merchant or "").lower() == "gas"


@db
@pytest.mark.bug("B-070")
async def test_a_bare_number_with_no_question_pending_is_still_not_an_expense(lab: Lab) -> None:
    await lab.say("75")
    assert await _expenses(lab) == []


@db
@pytest.mark.bug("B-070")
async def test_a_pending_bill_still_wins_over_the_question(lab: Lab) -> None:
    await lab.say("a pagar gás 60 dia 28")
    out = await lab.say("paguei gás")
    assert "Quanto foi" not in out
    assert "pago" in out.lower()


@db
@pytest.mark.bug("B-070")
async def test_words_that_are_not_an_item_do_not_start_the_question(lab: Lab) -> None:
    assert "Quanto foi" not in await lab.say("paguei tudo")


@db
@pytest.mark.bug("B-071")
async def test_isso_e_categoria_hits_the_item_just_talked_about_not_an_old_merchant(
    lab: Lab,
) -> None:
    lab.expense.return_value = _exp(4.5, "Padaria")
    await lab.say("Padaria 4,50")
    await _age_all(lab, 3)
    lab.expense.return_value = None
    await lab.say("paguei gas")
    await lab.say("75")
    out = await lab.say("isso e transporte")
    assert "Gas" in out and "Padaria" not in out
    cats = {r.merchant: r.category for r in await _expenses(lab)}
    assert cats["Gas"] == "transport" and cats["Padaria"] != "transport"


@db
@pytest.mark.bug("B-071")
async def test_isso_e_categoria_does_not_reach_back_to_an_old_entry(lab: Lab) -> None:
    lab.expense.return_value = _exp(4.5, "Padaria")
    await lab.say("Padaria 4,50")
    await _age_all(lab, 3)
    out = await lab.say("isso e transporte")
    assert "a qual lançamento" in out
    n = await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.category == "transport")
    )
    assert n == 0
