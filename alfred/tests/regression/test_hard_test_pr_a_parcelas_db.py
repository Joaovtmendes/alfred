"""B-083: "paguei 3 parcelas do celular" settles three instalments after a confirmation."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal
from alfred.models import Expense, RecurringItem
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _item() -> RecurringItem:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(RecurringItem))).scalars().one()


async def _paid() -> int:
    async with AsyncSessionLocal() as s:
        stmt = select(func.count()).select_from(Expense).where(Expense.status == "paid")
        return int(await s.scalar(stmt) or 0)


@db
@pytest.mark.bug("B-083")
async def test_three_instalments_are_settled_after_the_yes(lab: Lab) -> None:
    await lab.say("celular em 10x de 89,90 dia 5")
    ask = await lab.say("paguei 3 parcelas do celular")
    assert "3 × €89,90" in ask and "Faltariam 7" in ask
    assert await _paid() == 0  # nothing before the confirmation
    done = await lab.tap(lab.buttons[-1][0][0])
    assert "Faltam 7" in done
    assert await _paid() == 3
    assert (await _item()).installments_paid == 3


@db
@pytest.mark.bug("B-083")
async def test_no_cancels_and_an_unknown_plan_explains_how_to_create_it(lab: Lab) -> None:
    await lab.say("celular em 10x de 89,90 dia 5")
    await lab.say("paguei 2 parcelas do celular")
    await lab.tap(lab.buttons[-1][1][0])  # Não
    assert await _paid() == 0
    out = await lab.say("paguei 2 parcelas do carro")
    assert "carro" in out and "em 10x" in out


@db
@pytest.mark.bug("B-083")
async def test_more_than_the_remaining_asks_to_settle_all(lab: Lab) -> None:
    await lab.say("celular em 3x de 100 dia 5")
    ask = await lab.say("paguei 5 parcelas do celular")
    assert "só tem 3" in ask
    done = await lab.say("sim")
    assert "quitado" in done and await _paid() == 3
