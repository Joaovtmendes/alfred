"""V1-24 — defects found in the 01/10 hard test (recurrence lives in test_recurrence.py)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.clock import month_start, today_local
from alfred.models import Appointment, Note, WorkoutSession
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@db
@pytest.mark.asyncio
async def test_note_prefix_is_never_an_appointment(lab: Lab):
    await lab.say("nota: reunião com cliente segunda às 14h, levar contrato")
    notes = await lab.rows(select(Note.body).where(Note.member_id == lab.member_id))
    assert [n[0] for n in notes] == ["reunião com cliente segunda às 14h, levar contrato"]
    n_app = await lab.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.member_id == lab.member_id)
    )
    assert n_app == 0


@db
@pytest.mark.asyncio
async def test_mood_score_is_not_read_as_a_time(lab: Lab):
    reply = await lab.say("hoje sinto-me um 6 em 10")
    assert "já passaram" not in reply
    n_app = await lab.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.member_id == lab.member_id)
    )
    assert n_app == 0


@db
@pytest.mark.asyncio
async def test_workout_count_uses_the_period_asked(lab: Lab):
    today = today_local()
    prev = month_start(today).replace(day=1)
    older = (
        prev.replace(month=prev.month - 1)
        if prev.month > 1
        else prev.replace(year=prev.year - 1, month=12)
    )
    await lab.add(
        WorkoutSession(member_id=lab.member_id, activity_type="strength", workout_date=today),
        WorkoutSession(
            member_id=lab.member_id, activity_type="strength", workout_date=month_start(today)
        ),
        WorkoutSession(member_id=lab.member_id, activity_type="running", workout_date=today),
        WorkoutSession(member_id=lab.member_id, activity_type="strength", workout_date=older),
    )
    month = await lab.say("quantas vezes fiz musculação este mês?")
    assert "2x" in month and "este mês" in month
    assert "semana" not in month
    none = await lab.say("quantas vezes fiz natação este mês?")
    assert "este mês" in none and "semana" not in none


@db
@pytest.mark.asyncio
async def test_trip_with_explicit_range_keeps_its_dates(lab: Lab):
    from datetime import date

    from alfred.models import Trip

    await lab.say("viagem para Lisboa de 1 a 7 de outubro")
    (row,) = await lab.rows(
        select(Trip.started_at, Trip.ended_at).where(Trip.member_id == lab.member_id)
    )
    year = today_local().year
    assert row[0] == date(year, 10, 1)
