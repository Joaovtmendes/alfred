# ruff: noqa: E501
"""Second pass of the hard test 18–20/10, over the exported WhatsApp chat (every reply read).

These are the replies the checklist marked "Igual" but that were wrong on the phone: mostly the
model answering for a command that had no handler (and saying it did something).
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import today_local
from alfred.models import Expense, Trip, TripBudgetLine, WorkoutSession
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str, category: str = "overig") -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": category,
            "description": name, "type": "expense", "days_ago": 0}  # fmt: skip


async def _open_pending(lab: Lab) -> int:
    return await lab.scalar(
        select(func.count())
        .select_from(Expense)
        .where(Expense.member_id == lab.member_id, Expense.status == "to_pay")
    )


@db
@pytest.mark.bug("B-008")
async def test_the_amount_after_which_one_settles_that_pending_bill(lab: Lab) -> None:
    await lab.say("a pagar gás 60 dia 28")
    await lab.say("a pagar gás 75 dia 29")
    assert "Qual deles" in await lab.say("paguei gás")
    assert "75,00" in await lab.say("75")
    assert await _open_pending(lab) == 1


@db
@pytest.mark.bug("A-23")
async def test_the_category_name_shown_in_replies_is_understood(lab: Lab) -> None:
    lab.expense.return_value = _exp(15, "Uber")
    await lab.say("Uber 15")
    out = await lab.say("isso é transporte")
    assert "Transporte" in out
    cat = await lab.scalar(select(Expense.category).where(Expense.member_id == lab.member_id))
    assert cat == "transport"


@db
@pytest.mark.bug("B-061")
async def test_already_paid_settles_the_pending_bill_instead_of_a_new_expense(lab: Lab) -> None:
    await lab.say("a pagar luz 120 dia 25")
    out = await lab.say("luz 120 ja foi paga")
    assert "pago" in out.lower()
    assert await _open_pending(lab) == 0
    n = await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )
    assert n == 1


@db
@pytest.mark.bug("B-062")
async def test_apaga_isso_asks_to_delete_the_last_entry(lab: Lab) -> None:
    lab.expense.return_value = _exp(15, "Uber")
    await lab.say("Uber 15")
    assert "Apagar €15,00" in await lab.say("Apaga isso")


@db
@pytest.mark.bug("B-063")
async def test_blood_pressure_medication_is_not_refused_as_blood_pressure(lab: Lab) -> None:
    out = await lab.say("tomei o remédio da pressão")
    assert "pressão nem glicose" not in out
    assert "pressão nem glicose" in await lab.say("pressão 12 por 8")


@db
@pytest.mark.bug("B-064")
async def test_todays_training_plan_question_shows_the_plan(lab: Lab) -> None:
    out = await lab.say("plano de treino de hoje")
    assert "linha 1" not in out


@db
@pytest.mark.bug("B-065")
async def test_delete_a_workout_by_weekday(lab: Lab) -> None:
    today = today_local()
    await lab.add(
        WorkoutSession(
            member_id=lab.member_id, activity_type="running", workout_date=today, distance_km=5
        ),
        WorkoutSession(member_id=lab.member_id, activity_type="strength", workout_date=today),
        WorkoutSession(
            member_id=lab.member_id, activity_type="walking", workout_date=today - timedelta(days=1)
        ),
    )
    day = ["segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo"][today.weekday()]
    out = await lab.say(f"apaga o treino de {day}")
    assert "mais de um treino" in out
    assert "apagado" in (await lab.say(f"apaga a corrida de {day}")).lower()
    left = await lab.scalar(
        select(func.count())
        .select_from(WorkoutSession)
        .where(WorkoutSession.member_id == lab.member_id)
    )
    assert left == 2


@db
@pytest.mark.bug("B-066")
async def test_cheguei_em_casa_ends_the_trip(lab: Lab) -> None:
    await lab.say("criar viagem Bali")
    assert "Bali" in await lab.say("cheguei em casa")
    active = await lab.scalar(
        select(func.count())
        .select_from(Trip)
        .where(Trip.member_id == lab.member_id, Trip.active.is_(True))
    )
    assert active == 0


@db
@pytest.mark.bug("B-067")
async def test_trip_budget_without_da(lab: Lab) -> None:
    await lab.say("criar viagem Bali")
    await lab.say("orçamento viagem hospedagem 300")
    n = await lab.scalar(select(func.count()).select_from(TripBudgetLine))
    assert n == 1


@db
@pytest.mark.bug("B-068")
async def test_a_written_date_is_the_expense_date(lab: Lab) -> None:
    today = today_local()
    past = today - timedelta(days=10)
    lab.expense.return_value = _exp(130, "seguro saúde", "gezondheid")
    await lab.say(f"paguei 130 de seguro saúde, data {past:%d/%m}")
    when = await lab.scalar(select(Expense.expense_date).where(Expense.member_id == lab.member_id))
    assert when.date() == past


@db
@pytest.mark.bug("B-069")
async def test_warranty_wording(lab: Lab) -> None:
    assert "(1 mês)" in await lab.say("guarda serviço: teste, garantia 1 mes")
    assert "até 10 anos" in await lab.say("guarda serviço: telhado, 180 €, garantia 11 anos")


@db
@pytest.mark.bug("A-35")
async def test_unknown_business_categories_are_named(lab: Lab) -> None:
    await lab.say("modo empresa")
    out = await lab.say("sempre da empresa: software, transporte")
    assert "Transporte" in out and "software" in out


@db
@pytest.mark.bug("B-054")
async def test_a_health_word_and_a_number_is_never_a_bare_expense(lab: Lab) -> None:
    lab.expense.return_value = None
    await lab.say("humor 7")
    n = await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )
    assert n == 0
