"""B-084..B-087: a habit without a goal offers one; mood answers; "1 registro"."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from alfred import habits_ext
from alfred.db import AsyncSessionLocal
from alfred.models import Goal, HealthLog, Member
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _goals() -> list[Goal]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(Goal))).scalars().all())


async def _consent(lab: Lab) -> None:
    from datetime import UTC, datetime

    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        member.health_consent_at = datetime.now(UTC)
        await s.commit()


@pytest.mark.bug("B-087")
def test_singular_entry_in_the_five_languages() -> None:
    assert habits_ext.count_word(1, "pt") == "1 registro"
    assert habits_ext.count_word(3, "pt") == "3 registros"
    assert habits_ext.count_word(1, "nl") == "1 registratie"
    assert habits_ext.count_word(1, "en") == "1 entry"
    assert habits_ext.count_word(1, "fr") == "1 entrée"
    assert habits_ext.count_word(1, "de") == "1 Eintrag"


@pytest.mark.bug("B-086")
def test_mood_answers_by_range_with_an_emoji() -> None:
    assert "💙" in habits_ext.mood_reply(2, "pt")
    assert "🙂" in habits_ext.mood_reply(5, "pt")
    assert "😊" in habits_ext.mood_reply(8, "pt")
    assert "🌟" in habits_ext.mood_reply(10, "pt")


@db
@pytest.mark.bug("B-084")
async def test_a_habit_without_a_goal_offers_one_and_creates_it(lab: Lab) -> None:
    out = await lab.say("meditei hoje")
    assert "meditei" in out and "meta de *meditação*" in out
    assert [b[1] for b in lab.buttons[-1]] == ["Todo dia", "3x por semana", "Outra"]
    created = await lab.tap(lab.buttons[-1][1][0])
    assert "Meta criada" in created
    (goal,) = await _goals()
    assert (goal.target_value, goal.target_unit) == ("3x", "per week")
    assert goal.title.lower().startswith("meditação")


@db
@pytest.mark.bug("B-084")
async def test_the_goal_is_offered_once_per_habit(lab: Lab) -> None:
    await lab.say("meditei hoje")
    again = await lab.say("meditei hoje")  # same day: nothing new, no second offer
    assert "já está anotado" in again and lab.buttons[-1] == []


@db
@pytest.mark.bug("B-084")
async def test_other_frequency_is_typed(lab: Lab) -> None:
    await lab.say("meditei hoje")
    ask = await lab.tap(lab.buttons[-1][2][0])
    assert "Quantas vezes" in ask
    await lab.say("5")
    (goal,) = await _goals()
    assert goal.target_value == "5x"


@db
@pytest.mark.bug("B-085")
async def test_mood_above_ten_asks_before_logging_ten(lab: Lab) -> None:
    await _consent(lab)
    lab.health = AsyncMock(return_value={"log_type": "mood", "value": "12", "unit": None})
    ask = await lab.say("humor 12")
    assert "0 a 10" in ask
    async with AsyncSessionLocal() as s:
        assert (await s.execute(select(HealthLog))).scalars().all() == []
    done = await lab.tap(lab.buttons[-1][0][0])
    assert "🌟" in done
    async with AsyncSessionLocal() as s:
        (row,) = (await s.execute(select(HealthLog))).scalars().all()
    assert row.value == "10"


@db
@pytest.mark.bug("B-088")
async def test_the_quantity_is_kept_and_the_week_adds_up(lab: Lab) -> None:
    first = await lab.say("li 30 páginas")
    assert "30 páginas" in first and "Esta semana: 30 páginas" in first
    again = await lab.say("li 20 páginas")  # same day: added to the day, not refused
    assert "Somei" in again and "50 páginas" in again


@pytest.mark.bug("B-088")
def test_the_quantity_parser_reads_units_in_five_languages() -> None:
    assert habits_ext.parse_quantity("li 30 paginas") == (30.0, "páginas")
    assert habits_ext.parse_quantity("read 12,5 pages") == (12.5, "pages")
    assert habits_ext.parse_quantity("20 min yoga") == (20.0, "min")
    assert habits_ext.parse_quantity("meditei hoje") is None
