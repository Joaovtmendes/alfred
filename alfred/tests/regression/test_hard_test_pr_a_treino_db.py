"""B-092/B-093: "supino 4x10" is never an instalment; a misread plan edit asks "você quis dizer"."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal
from alfred.models import Expense, PendingAction, RecurringItem, WorkoutPlanItem
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _count(model) -> int:
    async with AsyncSessionLocal() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def _save_plan(lab: Lab) -> None:
    await lab.say("plano de treino:\nSegunda - Peito: Supino reto 3x8 60kg")
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    await lab.tap(f"plan_ok:{draft[0]}")


@db
@pytest.mark.bug("B-092")
async def test_yes_adds_the_exercise_to_the_plan_day(lab: Lab) -> None:
    await _save_plan(lab)
    await lab.say("treino de segunda crucifixo 3x12")
    done = await lab.tap(lab.buttons[-1][0][0])
    assert "entrou no plano de segunda" in done
    assert await _count(WorkoutPlanItem) == 2


@db
@pytest.mark.bug("B-092")
async def test_a_set_scheme_is_asked_about_and_never_becomes_an_instalment(lab: Lab) -> None:
    out = await lab.say("treino de segunda peito supino 4x10")
    assert "parece um exercício" in out and "plano de treino" in out
    assert await _count(RecurringItem) == 0 and await _count(Expense) == 0
    no = await lab.tap(lab.buttons[-1][1][0])
    assert "Nada foi registrado como despesa" in no
    assert await _count(RecurringItem) == 0


@db
@pytest.mark.bug("B-092")
async def test_four_times_ten_without_a_training_word_is_still_an_instalment(lab: Lab) -> None:
    out = await lab.say("celular 4x de 10")
    assert "Anotado: Celular em 4x" in out
    assert await _count(RecurringItem) == 1


@db
@pytest.mark.bug("B-093")
async def test_a_misread_plan_edit_asks_do_you_mean(lab: Lab) -> None:
    await _save_plan(lab)
    ask = await lab.say("muda o supino de segunda para 4 séries de 10")
    assert "Você quis dizer" in ask and "4x10" in ask
    done = await lab.tap(lab.buttons[-1][0][0])
    assert "4x10" in done
