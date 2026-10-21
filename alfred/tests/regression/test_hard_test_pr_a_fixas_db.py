"""B-081/B-082: a fixed bill with another day asks to change it; a monthly salary is income."""

from __future__ import annotations

import os
from datetime import datetime

import pytest
from sqlalchemy import select

from alfred.bills import income_reminders
from alfred.clock import local_tz, today_local
from alfred.db import AsyncSessionLocal
from alfred.models import Expense, RecurringItem
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _items(lab: Lab) -> list[RecurringItem]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(RecurringItem))).scalars().all())


async def _expenses(lab: Lab) -> list[Expense]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(Expense))).scalars().all())


@db
@pytest.mark.bug("B-081")
async def test_same_bill_with_another_day_asks_and_changes_the_day(lab: Lab) -> None:
    await lab.say("aluguel 900 todo mês dia 5")
    ask = await lab.say("aluguel 900 todo mês dia 10")
    assert "dia 5" in ask and "dia 10" in ask
    paid_q = await lab.tap(lab.buttons[-1][0][0])  # Mudar o dia
    assert "já foi paga" in paid_q
    (item,) = await _items(lab)
    assert item.due_day == 10
    first_due = item.next_due_date
    await lab.tap(lab.buttons[-1][0][0])  # Sim: this month's is paid
    (item,) = await _items(lab)
    assert item.next_due_date > first_due and item.due_day == 10


@db
@pytest.mark.bug("B-081")
async def test_keeping_the_day_changes_nothing(lab: Lab) -> None:
    await lab.say("aluguel 900 todo mês dia 5")
    await lab.say("aluguel 900 todo mês dia 10")
    out = await lab.tap(lab.buttons[-1][1][0])  # Manter
    assert "dia 5" in out
    (item,) = await _items(lab)
    assert item.due_day == 5


@db
@pytest.mark.bug("B-082")
async def test_a_salary_every_month_is_a_fixed_income_not_a_bill(lab: Lab) -> None:
    out = await lab.say("salário 3000 todo mês dia 25")
    assert "dia 25" in out and "caiu" in out
    (item,) = await _items(lab)
    assert (item.kind, item.category, item.due_day) == ("income", "inkomen", 25)
    assert "salário" not in (await lab.say("minhas contas fixas")).lower()


@db
@pytest.mark.bug("B-082")
async def test_on_the_day_the_salary_becomes_a_receivable_once(lab: Lab) -> None:
    await lab.say("salário 3000 todo mês dia 25")
    today = today_local()
    async with AsyncSessionLocal() as s:
        item = (await s.execute(select(RecurringItem))).scalars().one()
        item.next_due_date = today
        await s.commit()
    now = datetime.now(local_tz()).replace(hour=10)
    async with AsyncSessionLocal() as s:
        out = await income_reminders(s, now)
        await s.commit()
    assert len(out) == 1 and "recebi" in out[0].text
    async with AsyncSessionLocal() as s:
        assert await income_reminders(s, now) == []  # the same day again: nothing
    (exp,) = await _expenses(lab)
    assert (exp.status, exp.transaction_type, float(exp.amount)) == ("to_receive", "income", 3000.0)
    out = await lab.say("recebi o salário")
    assert "3.000" in out or "3000" in out
    (exp,) = await _expenses(lab)
    assert exp.status == "received"
