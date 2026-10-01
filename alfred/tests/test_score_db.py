"""V2-09 — health score: pure maths, history gate, no persistence, five languages."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import today_local
from alfred.models import Base, Goal, HabitLog, HealthLog, WorkoutSession
from alfred.score import MIN_HISTORY_DAYS, WEIGHTS, Counts, best_next_step, compute
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

FULL = Counts(
    workout_days_7=7,
    workout_days_30=30,
    habit_days_7=7,
    habit_days_30=30,
    health_days_14=14,
    active_goals=2,
    goals_with_activity_14=2,
)


def test_weights_sum_to_100() -> None:
    assert sum(WEIGHTS.values()) == 100


def test_bounds() -> None:
    assert compute(Counts()).total == 0
    assert compute(FULL).total == 100
    huge = Counts(**{k: 10_000 for k in Counts.__dataclass_fields__})
    assert compute(huge).total == 100  # clamped, never above 100


def test_components_are_isolated() -> None:
    w = compute(Counts(workout_days_7=7, workout_days_30=30))
    assert w.parts["workouts"] == 35 and w.parts["habits"] == 0 and w.parts["health"] == 0
    h = compute(Counts(habit_days_7=7, habit_days_30=30))
    assert h.parts["habits"] == 30 and h.parts["workouts"] == 0
    hl = compute(Counts(health_days_14=14))
    assert hl.parts["health"] == 15 and hl.parts["workouts"] == 0


def test_goals_excluded_when_none() -> None:
    no_goals = Counts(
        workout_days_7=7, workout_days_30=30, habit_days_7=7, habit_days_30=30, health_days_14=14
    )
    s = compute(no_goals)
    assert s.available["goals"] is False
    assert s.total == 100  # perfect without goals is still 100, not 80
    assert compute(Counts(active_goals=2, goals_with_activity_14=0)).available["goals"] is True


def test_best_next_step() -> None:
    assert best_next_step(FULL) is None
    kind, gain = best_next_step(Counts())  # type: ignore[misc]
    assert kind in {"workout", "habit", "health"} and gain > 0


# ── conversation ──────────────────────────────────────────────────────────────


async def _seed(lab: Lab, days: int) -> None:
    today = today_local()
    rows = []
    for i in range(days):
        d = today - timedelta(days=i)
        rows.append(WorkoutSession(member_id=lab.member_id, activity_type="run", workout_date=d))
        rows.append(HabitLog(member_id=lab.member_id, activity="water", log_date=d))
        rows.append(HealthLog(member_id=lab.member_id, log_type="sleep", value="7", log_date=d))
    await lab.add(*rows)


@db
async def test_insufficient_history(lab: Lab) -> None:
    await _seed(lab, MIN_HISTORY_DAYS - 2)
    reply = await lab.say("minha nota de saúde")
    assert "/100" not in reply and str(MIN_HISTORY_DAYS) in reply
    assert "conhecendo" in reply


@db
async def test_no_data_at_all(lab: Lab) -> None:
    assert "/100" not in await lab.say("health score")


@db
async def test_score_reply_and_disclaimer(lab: Lab) -> None:
    await _seed(lab, 14)
    reply = await lab.say("minha nota de saúde")
    assert "/100" in reply and "não orientação médica" in reply
    assert "Metas" not in reply.split("\n")[1]  # no goals → part left out
    assert "Sem metas ativas" in reply


@db
async def test_with_goal_counts_goals(lab: Lab) -> None:
    await _seed(lab, 14)
    g = Goal(member_id=lab.member_id, title="Correr", active=True)
    await lab.add(g)
    reply = await lab.say("minha nota")
    assert "Metas 0/20" in reply


@db
async def test_not_persisted(lab: Lab) -> None:
    """No table stores the score, and asking for it writes no row anywhere."""
    assert not [t for t in Base.metadata.tables if "score" in t]
    await _seed(lab, 14)

    async def total_rows() -> int:
        n = 0
        for table in Base.metadata.sorted_tables:
            if "member_id" in table.c:
                n += await lab.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(table.c.member_id == lab.member_id)
                )
        return n

    before = await total_rows()
    await lab.say("minha nota de saúde")
    assert await total_rows() == before


@db
@pytest.mark.parametrize(
    ("lang", "phrase", "marker"),
    [
        ("pt", "minha nota de saúde", "Sua nota"),
        ("nl", "mijn gezondheidsscore", "Je gezondheidsscore"),
        ("en", "my health score", "Your health score"),
        ("fr", "mon score santé", "Ton score santé"),
        ("de", "mein gesundheitsscore", "Dein Gesundheitsscore"),
    ],
)
async def test_five_languages(lab: Lab, lang: str, phrase: str, marker: str) -> None:
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.language = lang
        await s.commit()
    await _seed(lab, 14)
    assert marker in await lab.say(phrase)
