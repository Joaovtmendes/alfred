# ruff: noqa: E501
"""Training plan: change an exercise by command, and adjust the draft before confirming."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import select, update

from alfred.clock import now_local
from alfred.conversation import _t
from alfred.models import PendingAction, WorkoutPlanDay, WorkoutPlanItem
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

PLAN = (
    "plano de treino:\nSegunda - Peito: Supino reto 4x10 60kg, Supino inclinado 3x12 20kg, Crucifixo 3x12\n"
    "Quarta - Costas: Remada 4x10 50kg"
)


async def _saved(lab: Lab) -> None:
    await lab.say(PLAN)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    await lab.tap(f"plan_ok:{draft[0]}")


async def _item(lab: Lab, weekday: int, name: str) -> WorkoutPlanItem:
    rows = await lab.rows(
        select(WorkoutPlanItem)
        .join(WorkoutPlanDay, WorkoutPlanItem.day_id == WorkoutPlanDay.id)
        .where(WorkoutPlanItem.member_id == lab.member_id, WorkoutPlanDay.weekday == weekday)
    )
    (item,) = [r[0] for r in rows if r[0].exercise.lower() == name.lower()]
    return item


# ── active plan ───────────────────────────────────────────────────────────────


@db
async def test_change_the_load_of_the_saved_plan(lab: Lab) -> None:
    await _saved(lab)
    out = await lab.say("troca o supino reto de segunda para 62 kg")
    assert "4x10 · 62 kg" in out and "60 kg" in out and "segunda" in out
    item = await _item(lab, 0, "Supino reto")
    assert float(item.load_kg) == 62.0 and item.sets == 4 and item.reps == "10"


@db
async def test_change_sets_and_reps_and_load(lab: Lab) -> None:
    await _saved(lab)
    await lab.say("troca a remada de quarta para 5x8 55 kg")
    item = await _item(lab, 2, "Remada")
    assert (item.sets, item.reps, float(item.load_kg)) == (5, "8", 55.0)
    await lab.say("muda a remada de quarta para 4x10")
    item = await _item(lab, 2, "Remada")
    assert (item.sets, item.reps, float(item.load_kg)) == (4, "10", 55.0)


@db
async def test_a_load_can_be_added_to_an_exercise_without_one(lab: Lab) -> None:
    await _saved(lab)
    await lab.say("troca o crucifixo de segunda para 14,5 kg")
    assert float((await _item(lab, 0, "Crucifixo")).load_kg) == 14.5


@db
async def test_partial_name_must_be_unique(lab: Lab) -> None:
    await _saved(lab)
    out = await lab.say("troca o supino de segunda para 62 kg")
    assert "Supino reto" in out and "Supino inclinado" in out and "nome completo" in out
    assert float((await _item(lab, 0, "Supino reto")).load_kg) == 60.0
    out = await lab.say("troca o inclinado de segunda para 22 kg")
    assert float((await _item(lab, 0, "Supino inclinado")).load_kg) == 22.0
    assert out


@db
async def test_unknown_day_or_exercise(lab: Lab) -> None:
    await _saved(lab)
    assert "Não há treino" in await lab.say("troca o supino reto de sexta para 62 kg")
    assert "Não achei" in await lab.say("troca o leg press de segunda para 100 kg")
    assert "Não entendi" in await lab.say("troca o supino reto de segunda para bastante")


@db
async def test_without_a_plan(lab: Lab) -> None:
    assert await lab.say("troca o supino de segunda para 62 kg") == _t("plan_none", "pt")


@db
async def test_other_languages(lab: Lab) -> None:
    await _saved(lab)
    await lab.say("change remada on wednesday to 52 kg")
    assert float((await _item(lab, 2, "Remada")).load_kg) == 52.0
    await lab.say("wijzig remada op woensdag naar 53 kg")
    assert float((await _item(lab, 2, "Remada")).load_kg) == 53.0
    await lab.say("ändere remada am mittwoch auf 54 kg")
    assert float((await _item(lab, 2, "Remada")).load_kg) == 54.0


@db
async def test_unrelated_text_is_untouched(lab: Lab) -> None:
    await _saved(lab)
    assert await lab.say("troca de assunto, quanto gastei hoje?") == "[llm]"


# ── draft ─────────────────────────────────────────────────────────────────────


@db
async def test_adjust_button_keeps_the_draft_and_explains(lab: Lab) -> None:
    await lab.say(PLAN)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    out = await lab.tap(f"plan_adjust:{draft[0]}")
    assert "troca o supino" in out and "15 minutos" in out
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["plan_ok", "plan_cancel"]


@db
async def test_edit_changes_the_draft_not_the_plan(lab: Lab) -> None:
    await lab.say(PLAN)
    out = await lab.say("troca a remada de quarta para 55 kg")
    assert "55 kg" in out and "Nada foi salvo" in out
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == [
        "plan_ok",
        "plan_adjust",
        "plan_cancel",
    ]
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    await lab.tap(f"plan_ok:{draft[0]}")
    assert float((await _item(lab, 2, "Remada")).load_kg) == 55.0


@db
async def test_edit_extends_the_draft_and_expired_draft_is_refused(lab: Lab) -> None:
    await lab.say(PLAN)
    async with lab_session() as s:
        await s.execute(update(PendingAction).values(expires_at=now_local() - timedelta(minutes=1)))
        await s.commit()
    out = await lab.say("troca a remada de quarta para 55 kg")
    assert out == _t("plan_expired", "pt")
    assert await lab.rows(select(PendingAction)) == []


@db
async def test_adjust_after_a_tap_finds_nothing(lab: Lab) -> None:
    await lab.say(PLAN)
    (draft,) = await lab.rows(
        select(PendingAction.id).where(PendingAction.member_id == lab.member_id)
    )
    await lab.tap(f"plan_cancel:{draft[0]}")
    assert await lab.tap(f"plan_adjust:{draft[0]}") == _t("plan_gone", "pt")


def lab_session():
    from alfred.db import AsyncSessionLocal

    return AsyncSessionLocal()
