"""Sprint 1 bug regressions, exercised end to end against a real PostgreSQL.

Skipped unless ALFRED_TEST_DB=1 (see test_integration_db.py). Every LLM call and
the WhatsApp sender are patched; only the router, SQL and models are real.
"""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select

from alfred.conversation import handle_inbound
from alfred.db import AsyncSessionLocal, engine
from alfred.models import (
    Expense,
    Household,
    Member,
    MerchantCategoryOverride,
    Message,
    Trip,
)
from tests.conftest import make_message

pytestmark = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

EXPENSE = {
    "amount": 10.0,
    "currency": "EUR",
    "merchant": "Jumbo",
    "category": "supermarkt",
    "description": "compras",
    "type": "expense",
    "days_ago": 0,
}


@pytest.fixture(autouse=True)
async def _fresh_engine():
    yield
    await engine.dispose()


class Lab:
    """A member in a real DB plus a ``say()`` that runs one inbound message."""

    def __init__(self, member_id: uuid.UUID, household_id: uuid.UUID, phone: str) -> None:
        self.member_id, self.household_id, self.phone = member_id, household_id, phone
        self.sent: list[str] = []
        self.expense = AsyncMock(return_value=None)
        self.llm_reply = AsyncMock(return_value="[llm]")

    async def say(self, body: str) -> str:
        """Send one message; returns the reply text."""
        before = len(self.sent)

        async def fake_send(to: str, text: str) -> dict:
            self.sent.append(text)
            return {}

        with (
            patch("alfred.conversation.send_text", fake_send),
            patch("alfred.conversation.extract_expense", self.expense),
            patch("alfred.conversation.extract_habit", AsyncMock(return_value=None)),
            patch("alfred.conversation.extract_health_log", AsyncMock(return_value=None)),
            patch("alfred.conversation.extract_workout", AsyncMock(return_value=None)),
            patch("alfred.conversation.classify_query", AsyncMock(return_value=None)),
            patch("alfred.conversation.generate_reply", self.llm_reply),
        ):
            async with AsyncSessionLocal() as s:
                member = await s.get(Member, self.member_id)
                await handle_inbound(member, make_message(body), s)
                await s.commit()
        assert len(self.sent) == before + 1, f"expected exactly one reply, got {self.sent[before:]}"
        return self.sent[-1]

    async def scalar(self, stmt):
        async with AsyncSessionLocal() as s:
            return (await s.execute(stmt)).scalar()

    async def add(self, *rows) -> None:
        async with AsyncSessionLocal() as s:
            s.add_all(rows)
            await s.commit()


@pytest.fixture
async def lab():
    phone = "3160" + uuid.uuid4().hex[:7]
    async with AsyncSessionLocal() as s:
        hh = Household(name="bugs")
        s.add(hh)
        await s.flush()
        m = Member(household_id=hh.id, wa_phone=phone, consent_state="accepted", language="pt")
        s.add(m)
        await s.commit()
        lab = Lab(m.id, hh.id, phone)
    yield lab
    async with AsyncSessionLocal() as s:
        await s.execute(delete(Expense).where(Expense.member_id == lab.member_id))
        await s.execute(delete(Trip).where(Trip.member_id == lab.member_id))
        await s.execute(
            delete(MerchantCategoryOverride).where(
                MerchantCategoryOverride.member_id == lab.member_id
            )
        )
        await s.execute(delete(Message).where(Message.household_id == lab.household_id))
        await s.execute(delete(Member).where(Member.id == lab.member_id))
        await s.execute(delete(Household).where(Household.id == lab.household_id))
        await s.commit()


async def _active_trip(lab: Lab, destination: str = "Lisboa") -> None:
    from datetime import date

    await lab.add(
        Trip(
            id=uuid.uuid4(),
            member_id=lab.member_id,
            destination=destination,
            started_at=date.today(),
            active=True,
        )
    )


# ── 1.3 trip total ────────────────────────────────────────────────────────────


async def test_trip_total_is_not_double_counted(lab: Lab) -> None:
    await _active_trip(lab)
    lab.expense.return_value = dict(EXPENSE)
    first = await lab.say("gastei 10 no jumbo")
    second = await lab.say("gastei 10 no jumbo")
    assert "€10,00" in first.split("Lisboa")[-1]
    assert "€20,00" in second.split("Lisboa")[-1], second
    assert "€30,00" not in second


# ── 1.1 trips must not swallow expenses ───────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    ["20 euro terug gekregen", "voltei a gastar 20 no jumbo", "ns reis 12,50"],
)
async def test_trip_commands_do_not_swallow_expenses(lab: Lab, body: str) -> None:
    await _active_trip(lab)
    lab.expense.return_value = {**EXPENSE, "amount": 20.0, "type": "income"}
    await lab.say(body)
    n = await lab.scalar(select(func.count()).where(Expense.member_id == lab.member_id))
    assert n == 1, "the expense/income must be recorded"
    assert await lab.scalar(select(Trip.active).where(Trip.member_id == lab.member_id)) is True


async def test_trip_summary_does_not_start_a_trip_called_summary(lab: Lab) -> None:
    reply = await lab.say("trip summary")
    n = await lab.scalar(select(func.count()).where(Trip.member_id == lab.member_id))
    assert n == 0
    assert "Summary" not in reply


async def test_voltei_ends_the_active_trip(lab: Lab) -> None:
    await _active_trip(lab)
    await lab.say("voltei")
    assert await lab.scalar(select(Trip.active).where(Trip.member_id == lab.member_id)) is False


async def test_criar_viagem_starts_a_trip_with_clean_destination(lab: Lab) -> None:
    """Hard test B01 — the destination must not carry the budget or the dates."""
    await lab.say("criar viagem Portugal €500 de 1 a 7 de outubro")
    dest = await lab.scalar(select(Trip.destination).where(Trip.member_id == lab.member_id))
    assert dest == "Portugal"


# ── 1.2 category correction must not steal sentences ──────────────────────────


async def test_income_sentence_is_not_taken_as_a_category_correction(lab: Lab) -> None:
    lab.expense.return_value = {**EXPENSE, "amount": 2800.0, "type": "income"}
    await lab.say("recebi 2800 de salário e renda extra")
    assert await lab.scalar(select(func.count()).where(Expense.member_id == lab.member_id)) == 1
    assert (
        await lab.scalar(
            select(func.count()).where(MerchantCategoryOverride.member_id == lab.member_id)
        )
        == 0
    )


async def test_unknown_merchant_lookalike_falls_through(lab: Lab) -> None:
    reply = await lab.say("comprar comida e outros")
    assert reply == "[llm]"  # reached the normal chat, not swallowed as "Comprar Comida → overig"
    assert (
        await lab.scalar(
            select(func.count()).where(MerchantCategoryOverride.member_id == lab.member_id)
        )
        == 0
    )


async def test_known_merchant_correction_still_works(lab: Lab) -> None:
    lab.expense.return_value = dict(EXPENSE)
    await lab.say("gastei 10 no jumbo")
    reply = await lab.say("jumbo é restaurant")
    assert "Jumbo" in reply
    cat = await lab.scalar(
        select(MerchantCategoryOverride.category).where(
            MerchantCategoryOverride.member_id == lab.member_id
        )
    )
    assert cat == "restaurant"
    assert await lab.scalar(select(Expense.category).where(Expense.member_id == lab.member_id)) == (
        "restaurant"
    )


async def test_pronoun_correction_applies_to_the_last_expense(lab: Lab) -> None:
    lab.expense.return_value = dict(EXPENSE)
    await lab.say("gastei 10 no jumbo")
    await lab.say("isso não é wonen, é transport")
    assert await lab.scalar(select(Expense.category).where(Expense.member_id == lab.member_id)) == (
        "transport"
    )
