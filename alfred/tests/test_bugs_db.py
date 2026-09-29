"""Sprint 1 bug regressions, exercised end to end against a real PostgreSQL.

Skipped unless ALFRED_TEST_DB=1 (see test_integration_db.py). Every LLM call and
the WhatsApp sender are patched; only the router, SQL and models are real.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select

from alfred.db import engine
from alfred.models import (
    Expense,
    MerchantCategoryOverride,
    Trip,
)
from tests.labkit import Lab

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


# ── trip budget (hard test BUG-07) and single replies (BUG-05/06) ─────────────


async def test_trip_budget_is_parsed_and_remaining_is_shown(lab: Lab) -> None:
    reply = await lab.say("criar viagem Portugal €500 de 1 a 7 de outubro")
    assert "€500,00" in reply
    budget = await lab.scalar(select(Trip.budget).where(Trip.member_id == lab.member_id))
    assert budget == 500

    lab.expense.return_value = {**EXPENSE, "amount": 120.0, "merchant": "Hotel Lisboa"}
    reply = await lab.say("hotel Lisboa 120€ viagem Portugal")  # BUG-05: exactly one reply
    assert "€120,00" in reply and "restante €380,00" in reply  # BUG-07


async def test_trip_over_budget_is_flagged(lab: Lab) -> None:
    await lab.say("criar viagem Roma €100")
    lab.expense.return_value = {**EXPENSE, "amount": 130.0}
    reply = await lab.say("gastei 130 no jumbo")
    assert "€30,00 acima do orçamento" in reply


async def test_trip_without_budget_has_no_remaining_text(lab: Lab) -> None:
    await lab.say("viagem a Lisboa")
    lab.expense.return_value = dict(EXPENSE)
    reply = await lab.say("gastei 10 no jumbo")
    assert "restante" not in reply and "acima do orçamento" not in reply


@pytest.mark.parametrize(
    ("lang", "ongoing", "header"),
    [("nl", "lopend", "Reis:"), ("en", "ongoing", "Trip:"), ("de", "laufend", "Reise:")],
)
async def test_trip_summary_and_list_follow_the_member_language(lab, lang, ongoing, header):
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.language = lang
        await s.commit()
    await lab.say(
        {"nl": "nieuwe reis naar Berlijn", "en": "trip to Berlin", "de": "reise nach Wien"}[lang]
    )
    lab.expense.return_value = dict(EXPENSE)
    await lab.say({"nl": "Jumbo 10 euro", "en": "spent 10 at jumbo", "de": "Jumbo 10 euro"}[lang])
    summary = await lab.say({"nl": "reiskosten", "en": "trip summary", "de": "reisekosten"}[lang])
    assert summary.startswith(header)
    listing = await lab.say({"nl": "mijn reizen", "en": "my trips", "de": "meine reisen"}[lang])
    assert ongoing in listing
