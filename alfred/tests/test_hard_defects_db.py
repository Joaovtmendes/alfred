"""Defects found by the 30/09 hard test (real PostgreSQL, ALFRED_TEST_DB=1)."""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import func, select

from alfred.conversation import _fuzzy_contains
from alfred.models import Goal, HabitLog, HealthLog, Task, Trip
from alfred.parsing import parse_trip_start_date
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def test_fuzzy_contains_is_accent_and_stem_tolerant() -> None:
    assert _fuzzy_contains("aniversário", "comprar presente para o aniversario")
    assert _fuzzy_contains("corrida", "correr 3x por semana")
    assert not _fuzzy_contains("natação", "correr 3x por semana")
    assert not _fuzzy_contains("de", "correr 3x por semana")


def test_trip_start_date_from_range() -> None:
    today = date(2026, 9, 30)
    assert parse_trip_start_date("viagem Lisboa de 1 a 7 de outubro", today) == date(2026, 10, 1)
    assert parse_trip_start_date("trip Rome 3 mar 2027", today) == date(2027, 3, 3)
    assert parse_trip_start_date("viagem Lisboa", today) is None


@db
@pytest.mark.parametrize("word", ["sim", "yes", "ja", "Ok!"])
async def test_bare_yes_is_neutral(lab: Lab, word: str) -> None:
    reply = await lab.say(word)
    assert "nada esperando" in reply or "niets" in reply or "anything waiting" in reply
    assert "despesa por linha" not in reply


@db
async def test_unrecognised_health_message_gets_neutral_fallback(lab: Lab) -> None:
    lab.llm_reply.return_value = "[llm]"
    reply = await lab.say("peso 75kg pressão 12/8")
    assert "despesa por linha" not in reply


@db
async def test_delete_task_ignores_accents(lab: Lab) -> None:
    await lab.add(Task(member_id=lab.member_id, body="comprar presente para o aniversário"))
    reply = await lab.say("apaga tarefa de aniversario")
    assert "Não encontrei" not in reply
    n = await lab.scalar(
        select(func.count()).select_from(Task).where(Task.member_id == lab.member_id)
    )
    assert n == 0


@db
async def test_complete_goal_by_stem(lab: Lab) -> None:
    await lab.add(Goal(member_id=lab.member_id, title="correr 3x por semana", active=True))
    reply = await lab.say("meta de corrida concluída")
    assert "Não encontrei" not in reply
    goal = await lab.scalar(select(Goal).where(Goal.member_id == lab.member_id))
    assert goal.active is False


@db
async def test_question_is_not_logged_as_habit(lab: Lab) -> None:
    await lab.say("bebi água suficiente hoje?")
    n = await lab.scalar(
        select(func.count()).select_from(HabitLog).where(HabitLog.member_id == lab.member_id)
    )
    assert n == 0


@db
async def test_trip_uses_given_start_date(lab: Lab) -> None:
    await lab.say("criar viagem Lisboa de 1 a 7 de outubro")
    trip = await lab.scalar(select(Trip).where(Trip.member_id == lab.member_id))
    assert trip is not None and trip.started_at.month == 10 and trip.started_at.day == 1


@db
@pytest.mark.parametrize(
    "text,data",
    [
        ("sinto-me um 6 em 10", {"log_type": "mood", "value": "6", "unit": "/10"}),
        ("ontem dormi 6h30", {"log_type": "sleep", "value": "6.5", "unit": "hours", "days_ago": 1}),
        ("mais 500ml de água", {"log_type": "water", "value": "0.5", "unit": "L"}),
    ],
)
async def test_midsentence_health_logs_reach_the_extractor(lab: Lab, text: str, data: dict) -> None:
    lab.health.return_value = {"notes": None, "days_ago": 0, "unit": None, **data}
    await lab.say(text)
    lab.health.assert_awaited_once()
    row = await lab.scalar(select(HealthLog).where(HealthLog.member_id == lab.member_id))
    assert row is not None and row.log_type == data["log_type"]


@db
async def test_swimming_reaches_the_workout_extractor(lab: Lab) -> None:
    lab.workout.return_value = {
        "activity_type": "swimming",
        "duration_minutes": 30,
        "distance_km": None,
        "notes": None,
        "days_ago": 0,
    }
    await lab.say("nadei 30 minutos")
    lab.workout.assert_awaited_once()


def _exp(amount: float, name: str, category: str = "supermarkt", kind: str = "expense") -> dict:
    return {
        "amount": amount,
        "currency": "EUR",
        "merchant": name,
        "category": category,
        "description": name,
        "type": kind,
        "days_ago": 0,
    }


@db
async def test_confirmation_shows_month_total_from_second_expense_of_a_category(lab: Lab) -> None:
    lab.expense.return_value = _exp(40, "Jumbo")
    first = await lab.say("Jumbo 40")
    assert "este mês" not in first and "no mês" not in first  # nothing to add yet
    lab.expense.return_value = _exp(25, "Albert Heijn")
    second = await lab.say("Albert Heijn 25")
    assert "€65,00" in second and "supermercado" in second.lower()


@db
async def test_month_total_ignores_income_and_other_categories(lab: Lab) -> None:
    lab.expense.return_value = _exp(40, "Jumbo")
    await lab.say("Jumbo 40")
    lab.expense.return_value = _exp(2800, "Salário", category="inkomen", kind="income")
    income = await lab.say("recebi 2800 de salário")
    assert "este mês" not in income and "no mês" not in income
    lab.expense.return_value = _exp(12, "Uber", category="vervoer")
    other = await lab.say("Uber 12")
    assert "este mês" not in other and "no mês" not in other
