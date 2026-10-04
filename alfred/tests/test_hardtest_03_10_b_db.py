"""Hard test 03/10, group B — workouts (defects 4, 18, 5)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.conversation import _workout_dur
from alfred.models import Expense, HabitLog, WorkoutSession
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _wo(kind: str, minutes: int | None, km: float | None = None) -> dict:
    return {
        "activity_type": kind,
        "duration_minutes": minutes,
        "distance_km": km,
        "notes": None,
        "days_ago": 0,
    }


@db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "kind", "minutes"),
    [
        ("fiz 50 minutos de musculação", "gym", 50),
        ("fiz yoga durante 20 minutos", "yoga", 20),
        ("fiz pilates 40 min", "pilates", 40),
    ],
)
async def test_did_an_activity_for_some_minutes_is_a_workout(
    lab: Lab, text: str, kind: str, minutes: int
):
    """Defects 4 and 18: "fiz …" was an expense / the habit "fiz yoga"."""
    lab.workout.return_value = _wo(kind, minutes)
    lab.habit.return_value = {"activity": "fiz yoga", "days_ago": 0}
    lab.expense.return_value = None
    reply = await lab.say(text)
    lab.workout.assert_awaited_once()
    lab.habit.assert_not_awaited()
    rows = await lab.rows(
        select(WorkoutSession.duration_minutes).where(WorkoutSession.member_id == lab.member_id)
    )
    assert [r[0] for r in rows] == [minutes]
    assert (
        await lab.scalar(
            select(func.count()).select_from(HabitLog).where(HabitLog.member_id == lab.member_id)
        )
        == 0
    )
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 0
    )
    assert "didn't record" not in reply.lower()


def test_duration_and_distance_are_both_shown():
    """Defect 5: the 5 km vanished from the confirmation when minutes were given."""
    assert _workout_dur(30, 5.0, "pt") == "30 min · 5 km"
    assert _workout_dur(30, None, "pt") == "30 min"
    assert _workout_dur(None, 5.5, "pt") == "5,5 km"
    assert _workout_dur(None, None, "pt") == ""


def test_a_plan_in_the_natural_layout_parses():
    """Defect 6: "Segunda: peito" with one exercise per line was "Não entendi a linha 2"."""
    from alfred.training import parse_plan

    plan = parse_plan(
        "Segunda: peito\n- supino 4x10 60kg\n- crucifixo 3x12\n"
        "Quarta: pernas\nagachamento 4x8 80kg\nleg press 3x12"
    )
    assert isinstance(plan, list) and [d.weekday for d in plan] == [0, 2]
    assert plan[0].title == "peito"
    assert [i.exercise for i in plan[0].items] == ["supino", "crucifixo"]
    assert plan[0].items[0].load_kg == 60
    assert [i.exercise for i in plan[1].items] == ["agachamento", "leg press"]


def test_the_one_line_layout_still_parses_and_a_bad_line_is_still_pointed_at():
    from alfred.training import parse_plan

    assert isinstance(parse_plan("Segunda - Peito: Supino 4x10 60kg, Crucifixo 3x12"), list)
    assert parse_plan("Segunda: supino 4x10\nBlablabla: x") == 2
