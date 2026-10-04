"""Hard test 03/10, group A — data loss and wrong handling (defects 19, 9, 8)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.models import Appointment, Expense, HabitLog, Note, Task
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str, category: str):
    return {
        "amount": amount,
        "currency": "EUR",
        "merchant": name,
        "category": category,
        "description": name,
        "type": "expense",
        "days_ago": 0,
    }


@db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "amount", "name", "category"),
    [
        ("cinema 24", 24.0, "Cinema", "entertainment"),
        ("café 3,50", 3.5, "Café", "restaurant"),
        ("uber 12", 12.0, "Uber", "transport"),
    ],
)
async def test_a_word_and_a_bare_amount_is_an_expense_never_a_habit(
    lab: Lab, text: str, amount: float, name: str, category: str
):
    """Defect 19: "cinema 24" was logged as the habit "cinema" and the 24 EUR was lost."""
    lab.habit.return_value = {"activity": name.lower(), "days_ago": 0}  # the LLM guessing wrong
    lab.expense.return_value = _exp(amount, name, category)
    reply = await lab.say(text)
    habits = await lab.scalar(
        select(func.count()).select_from(HabitLog).where(HabitLog.member_id == lab.member_id)
    )
    assert habits == 0
    lab.habit.assert_not_awaited()
    rows = await lab.rows(select(Expense.amount).where(Expense.member_id == lab.member_id))
    assert [float(r[0]) for r in rows] == [amount]
    assert "24" in reply or "3,50" in reply or "12" in reply  # the amount is in the confirmation


@db
@pytest.mark.asyncio
async def test_a_number_with_a_unit_is_still_free_text_for_the_habit_classifier(lab: Lab):
    lab.habit.return_value = {"activity": "leitura", "days_ago": 0}
    await lab.say("leitura 30 min")  # "min" says it is not money
    lab.habit.assert_awaited_once()
    habits = await lab.scalar(
        select(func.count()).select_from(HabitLog).where(HabitLog.member_id == lab.member_id)
    )
    assert habits == 1


@db
@pytest.mark.asyncio
async def test_a_reminder_prefix_is_not_part_of_the_appointment_title(lab: Lab):
    """Defect 9: "lembrete: ligar ao dentista ..." became the appointment "Lembrete: ligar ..."."""
    await lab.say("lembrete: ligar ao dentista amanhã às 14h")
    rows = await lab.rows(select(Appointment.title).where(Appointment.member_id == lab.member_id))
    assert [r[0] for r in rows] == ["Ligar ao dentista"]


@db
@pytest.mark.asyncio
async def test_the_same_appointment_is_not_created_twice(lab: Lab):
    """Defect 9: "dentista amanhã às 14h" and then the reminder for it made two entries."""
    await lab.say("dentista amanhã às 14h")
    reply = await lab.say("lembrete: ligar ao dentista amanhã às 14h")
    rows = await lab.rows(select(Appointment.title).where(Appointment.member_id == lab.member_id))
    assert [r[0] for r in rows] == ["Dentista"]
    assert "Dentista" in reply and "já" in reply.lower()


@db
@pytest.mark.asyncio
async def test_two_different_things_at_the_same_time_are_both_kept(lab: Lab):
    await lab.say("dentista amanhã às 14h")
    await lab.say("reunião com a Ana amanhã às 14h")
    n = await lab.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.member_id == lab.member_id)
    )
    assert n == 2


@db
@pytest.mark.asyncio
async def test_notes_and_tasks_keep_the_text_the_way_it_was_written(lab: Lab):
    """Defect 8: "Nota: Ligar pro Dentista" was stored in lower case."""
    await lab.say("Nota: Ligar pro Dentista Amanhã, falar com a Dra. Silva")
    await lab.say("Tarefa: Enviar Relatório para o João")
    notes = await lab.rows(select(Note.body).where(Note.member_id == lab.member_id))
    tasks = await lab.rows(select(Task.body).where(Task.member_id == lab.member_id))
    assert [n[0] for n in notes] == ["Ligar pro Dentista Amanhã, falar com a Dra. Silva"]
    assert [t[0] for t in tasks] == ["Enviar Relatório para o João"]
