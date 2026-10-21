"""B-077/B-078: bills without a date ask for it; any bill asks once whether it repeats."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from alfred.db import AsyncSessionLocal
from alfred.models import Expense, RecurringItem
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _bills(lab: Lab) -> list[Expense]:
    async with AsyncSessionLocal() as s:
        stmt = select(Expense).where(Expense.status == "to_pay")
        return list((await s.execute(stmt)).scalars().all())


async def _fixed(lab: Lab) -> list[RecurringItem]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(RecurringItem))).scalars().all())


@db
@pytest.mark.bug("B-077")
async def test_a_bill_without_a_date_asks_when_it_is_due(lab: Lab) -> None:
    reply = await lab.say("a pagar internet 45")
    assert "Quando vence" in reply
    assert [b[0] for b in lab.buttons[-1]] != []
    assert await _bills(lab) == []  # nothing saved until the date is known
    out = await lab.say("dia 28")
    assert "Essa conta repete" in out
    (bill,) = await _bills(lab)
    assert bill.expense_date.day == 28


@db
@pytest.mark.bug("B-077")
async def test_the_today_button_dates_the_bill(lab: Lab) -> None:
    await lab.say("a pagar internet 45")
    await lab.tap(lab.buttons[-1][0][0])
    (bill,) = await _bills(lab)
    assert float(bill.amount) == 45.0


@db
@pytest.mark.bug("B-078")
async def test_repeating_bill_becomes_a_fixed_bill_from_next_month(lab: Lab) -> None:
    out = await lab.say("a pagar gás 60 dia 28")
    assert "Essa conta repete todo mês" in out
    done = await lab.say("sim")
    assert "conta fixa" in done
    (item,) = await _fixed(lab)
    assert (item.name.lower(), item.due_day, item.frequency) == ("gás", 28, "monthly")
    (bill,) = await _bills(lab)
    assert item.next_due_date > bill.expense_date.date()  # this month's one is the to-pay entry


@db
@pytest.mark.bug("B-078")
async def test_not_repeating_leaves_no_fixed_bill(lab: Lab) -> None:
    await lab.say("a pagar gás 60 dia 28")
    await lab.say("não")
    assert await _fixed(lab) == []
    assert len(await _bills(lab)) == 1
