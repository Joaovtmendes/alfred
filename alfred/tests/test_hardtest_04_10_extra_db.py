"""Leftovers of the 03/10 hard test: repeated habit, savings goal progress, default day chip."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from alfred.models import Expense, Goal, HabitLog
from tests.labkit import Lab
from tests.test_panel_cards import _card, _get, _token

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@db
@pytest.mark.asyncio
async def test_the_same_habit_twice_in_a_day_is_one_check_in(lab: Lab):
    """Defect 18: the second "meditei" of the day said "Mais um dia!" and counted twice."""
    first = await lab.say("meditei")
    second = await lab.say("meditei")
    n = await lab.scalar(
        select(func.count()).select_from(HabitLog).where(HabitLog.member_id == lab.member_id)
    )
    assert n == 1
    assert "mais um dia" not in second.lower() and second != first
    assert "hoje" in second.lower()


@db
@pytest.mark.asyncio
async def test_a_savings_goal_shows_progress_in_euro(lab: Lab, client):
    """Defect 12: "poupar 5000€" showed nothing; it now shows what was saved since it was set."""
    await lab.add(
        Goal(
            member_id=lab.member_id,
            title="poupar 5000€",
            target_value="5000",
            target_unit="EUR",
            created_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
    )

    def row(kind, amount, month):
        return Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type=kind,
            amount=amount,
            merchant="x",
            category="inkomen" if kind == "income" else "overig",
            status="received" if kind == "income" else "paid",
            expense_date=datetime(2026, month, 10, 12, tzinfo=UTC),
        )

    await lab.add(row("income", 3000, 9), row("expense", 800, 9), row("expense", 5000, 8))
    body = await _get(client, await _token(lab.member_id), "health")
    item = _card(body, "goals")["items"][0]
    assert item["money"] is True
    assert item["saved"] == 2200.0 and item["target_amount"] == 5000.0 and item["saved_pct"] == 44


def test_a_habit_named_by_its_verb_reads_as_a_noun_in_the_streak_sentence():
    """ "Já são 3 dias seguidos de meditei" → "… de meditação"."""
    from alfred.conversation import _habit_label

    assert _habit_label("meditei", "pt") == "meditação"
    assert _habit_label("meditated", "en") == "meditation"
    assert _habit_label("fiz yoga", "pt") == "yoga"
    assert _habit_label("bebi água", "pt") == "água"
    assert _habit_label("leitura", "pt") == "leitura"  # already a noun
    assert _habit_label("meditei", "nl") == "meditei"  # no map for this language: untouched


@db
@pytest.mark.asyncio
async def test_the_streak_reply_uses_the_noun(lab: Lab):
    await lab.say("meditei")
    reply = await lab.say("quantos dias seguidos de meditação")
    assert "meditei" not in reply.lower() and "meditação" in reply.lower()
