"""B-089..B-091: weight without pressure, workout comparison, "treinos de hoje"."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from alfred.clock import today_local
from alfred.db import AsyncSessionLocal
from alfred.models import HealthLog, Member, WorkoutSession
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _weights() -> list[HealthLog]:
    async with AsyncSessionLocal() as s:
        stmt = select(HealthLog).where(HealthLog.log_type == "weight")
        return list((await s.execute(stmt)).scalars().all())


@db
@pytest.mark.bug("B-089")
async def test_weight_asks_for_the_health_consent_first(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Member).where(Member.id == lab.member_id).values(health_consent_at=None)
        )
        await s.commit()
    out = await lab.say("peso 75 kg")
    assert "autorização" in out
    assert await _weights() == []


@db
@pytest.mark.bug("B-089")
async def test_weight_is_saved_with_a_neutral_change_and_can_be_deleted(lab: Lab) -> None:
    first = await lab.say("peso 75 kg")
    assert first.startswith("Anotado: 75 kg.")
    async with AsyncSessionLocal() as s:  # make the first entry older
        await s.execute(update(HealthLog).values(log_date=today_local() - timedelta(days=7)))
        await s.commit()
    second = await lab.say("peso 74,5")
    assert "74,5 kg" in second and "0,5 kg a menos" in second
    assert len(await _weights()) == 2
    assert "apaguei" in await lab.say("apaga peso")
    assert await _weights() == []
    assert "não parece certo" in await lab.say("peso 7 kg")


@db
@pytest.mark.bug("B-090")
async def test_a_workout_is_compared_with_the_last_of_the_same_activity(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        s.add(
            WorkoutSession(
                member_id=lab.member_id,
                activity_type="running",
                duration_minutes=35,
                distance_km=5.0,
                workout_date=today_local() - timedelta(days=3),
            )
        )
        await s.commit()
    lab.workout.return_value = {
        "activity_type": "running", "duration_minutes": 30, "distance_km": 5.0, "days_ago": 0
    }  # fmt: skip
    out = await lab.say("corri 5 km em 30 minutos")
    assert "5 min mais rápido" in out


@db
@pytest.mark.bug("B-091")
async def test_workouts_of_today_lists_all_with_or_without_duration(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        s.add_all(
            [
                WorkoutSession(
                    member_id=lab.member_id,
                    activity_type="running",
                    duration_minutes=30,
                    workout_date=today_local(),
                ),
                WorkoutSession(
                    member_id=lab.member_id, activity_type="yoga", workout_date=today_local()
                ),
            ]
        )
        await s.commit()
    out = await lab.say("treinos de hoje")
    assert "(2)" in out and "corrida, 30 min" in out and "• ioga" in out
    assert "Nenhum treino" in await lab.say("treinos de ontem")
