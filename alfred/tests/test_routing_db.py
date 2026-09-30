"""Router behaviour: shadowed handlers, accents, destructive commands, day boundaries.

Real PostgreSQL (ALFRED_TEST_DB=1); LLM and WhatsApp sender patched (see labkit).
Each case is a real message that used to reach the wrong handler.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from alfred.clock import today_local
from alfred.conversation import _t
from alfred.models import HabitLog, HealthLog, Task, WorkoutSession
from tests.labkit import Lab

pytestmark = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _workouts(lab: Lab, *days_ago: int, activity: str = "running") -> None:
    await lab.add(
        *[
            WorkoutSession(
                member_id=lab.member_id,
                activity_type=activity,
                workout_date=today_local() - timedelta(days=d),
            )
            for d in days_ago
        ]
    )


# ── shadowed handlers ─────────────────────────────────────────────────────────


async def test_quantas_vezes_corri_goes_to_workouts_not_habits(lab: Lab) -> None:
    await _workouts(lab, 0, 2)
    reply = await lab.say("quantas vezes corri esta semana?")
    assert reply == _t("workout_activity_summary", "pt", activity="corrida", n=2)


async def test_como_vai_a_vida_is_chat_not_the_goals_list(lab: Lab) -> None:
    assert await lab.say("como vai a vida?") == "[llm]"


async def test_gastos_em_restaurante_keeps_its_category(lab: Lab) -> None:
    lab.classify.return_value = {
        "query_type": "category",
        "category": "restaurant",
        "period": "current_month",
    }
    reply = await lab.say("gastos em restaurante")
    lab.classify.assert_awaited_once()  # the plain "gastos" summary must not steal it
    assert "Restaurant" in reply


async def test_bare_gastos_is_still_the_summary(lab: Lab) -> None:
    await lab.say("gastos")
    lab.classify.assert_not_awaited()


# ── accents ───────────────────────────────────────────────────────────────────


async def test_accented_mood_question_is_understood(lab: Lab) -> None:
    await lab.add(
        HealthLog(member_id=lab.member_id, log_type="mood", value="7", log_date=today_local())
    )
    reply = await lab.say("como está o meu humor?")
    assert reply != "[llm]" and "7" in reply


async def test_accented_sleep_and_medication_queries(lab: Lab) -> None:
    for text in ("quantas horas dormi?", "aderência da medicação"):
        assert await lab.say(text) != "[llm]", text


async def test_accented_cancel_reminder_keeps_the_accented_text(lab: Lab) -> None:
    reply = await lab.say("cancela lembrete de medicação")
    assert reply == _t("lembrete_cancel_not_found", "pt")


# ── 7-day window ──────────────────────────────────────────────────────────────


async def test_treinos_window_is_seven_days_not_eight(lab: Lab) -> None:
    await _workouts(lab, 6, 7)  # 6 days ago is in, 7 days ago is out
    reply = await lab.say("treinos")
    assert reply.splitlines()[0] == _t("workout_summary_header", "pt", n=1)


# ── destructive commands are literal and scoped ───────────────────────────────


async def test_delete_task_treats_percent_literally(lab: Lab) -> None:
    await lab.add(Task(member_id=lab.member_id, body="fazer 100 coisas"))
    reply = await lab.say("apaga tarefa 1%0")  # was: LIKE '%1%0%' deleted the task
    assert reply == _t("task_delete_not_found", "pt")
    assert await lab.scalar(select(func.count()).where(Task.member_id == lab.member_id)) == 1


async def test_done_treats_underscore_literally(lab: Lab) -> None:
    await lab.add(Task(member_id=lab.member_id, body="ligar ao banco"))
    reply = await lab.say("feito: l_gar")
    assert reply == _t("task_not_found", "pt")


async def test_delete_todays_workout_never_removes_another_day(lab: Lab) -> None:
    await _workouts(lab, 1)  # only yesterday
    reply = await lab.say("apaga o treino de hoje")
    assert reply == _t("workout_delete_not_found", "pt")
    assert (
        await lab.scalar(select(func.count()).where(WorkoutSession.member_id == lab.member_id)) == 1
    )


# ── task text and due dates ───────────────────────────────────────────────────


async def test_task_text_is_not_cut_at_a_word_that_merely_contains_by(lab: Lab) -> None:
    await lab.say("tarefa: ir ao hobby shop")
    body = await lab.scalar(select(Task.body).where(Task.member_id == lab.member_id))
    assert body == "ir ao hobby shop"  # was: "ir ao hob"


async def test_task_keeps_full_text_when_the_date_does_not_parse(lab: Lab) -> None:
    await lab.say("tarefa: ligar ao Rui até logo")
    row = await lab.scalar(select(Task).where(Task.member_id == lab.member_id))
    assert row.body.endswith("até logo") and row.due_date is None


async def test_task_with_a_real_due_date_is_split(lab: Lab) -> None:
    await lab.say("tarefa: pagar renda até 2030-01-15")
    row = await lab.scalar(select(Task).where(Task.member_id == lab.member_id))
    assert row.body == "pagar renda" and row.due_date == date(2030, 1, 15)


# ── Amsterdam day, not UTC day ────────────────────────────────────────────────


async def test_habit_logged_just_after_local_midnight_lands_on_the_local_day(lab: Lab) -> None:
    local_day = date(2026, 9, 29)  # 22:30 UTC on the 28th is 00:30 on the 29th in Amsterdam
    with patch("alfred.conversation.today_local", return_value=local_day):
        await lab.say("meditei hoje")
    d = await lab.scalar(select(HabitLog.log_date).where(HabitLog.member_id == lab.member_id))
    assert d == local_day


# ── second-round review findings ──────────────────────────────────────────────


async def test_treinos_deste_mes_reaches_the_monthly_handler(lab: Lab) -> None:
    from alfred.clock import month_name

    await _workouts(lab, 0)
    reply = await lab.say("treinos deste mês")  # was: always answered by the weekly list
    header = _t(
        "workout_month_header",
        "pt",
        month=month_name(today_local(), "pt", year=True),
        n=1,
        km=0,
        min=0,
    )
    assert reply.splitlines()[0] == header


async def test_treinos_de_mes_passado_uses_the_previous_month(lab: Lab) -> None:
    from alfred.clock import month_name, prev_month_start

    await _workouts(lab, 0)  # today only, so last month is empty
    reply = await lab.say("treinos mês passado")
    header = _t(
        "workout_month_header",
        "pt",
        month=month_name(prev_month_start(today_local()), "pt", year=True),
        n=0,
        km=0,
        min=0,
    )
    assert reply.splitlines()[0] == header


async def test_treinos_de_um_mes_por_nome(lab: Lab) -> None:
    await _workouts(lab, 0)
    reply = await lab.say("treinos de agosto")
    assert "Agosto" in reply.splitlines()[0]


async def test_delete_yesterdays_workout_only_removes_yesterdays(lab: Lab) -> None:
    await _workouts(lab, 0, 1, 3)
    await lab.say("apaga o treino de ontem")
    days = sorted(
        d
        for (d,) in (
            await lab.rows(
                select(WorkoutSession.workout_date).where(WorkoutSession.member_id == lab.member_id)
            )
        )
    )
    assert days == [today_local() - timedelta(days=3), today_local()]


async def test_delete_workout_of_an_unsupported_day_deletes_nothing(lab: Lab) -> None:
    await _workouts(lab, 0)
    await lab.say("apaga o treino de segunda")  # not a command we understand
    n = await lab.scalar(select(func.count()).where(WorkoutSession.member_id == lab.member_id))
    assert n == 1


async def test_actually_i_spent_is_a_new_expense_not_an_amount_correction(lab: Lab) -> None:
    from alfred.models import Expense

    lab.expense.return_value = {
        "amount": 10.0,
        "currency": "EUR",
        "merchant": "Jumbo",
        "category": "supermarkt",
        "description": "x",
        "type": "expense",
        "days_ago": 0,
    }
    await lab.say("gastei 10 no jumbo")
    lab.expense.return_value = {
        "amount": 20.0,
        "currency": "EUR",
        "merchant": "Lidl",
        "category": "supermarkt",
        "description": "x",
        "type": "expense",
        "days_ago": 0,
    }
    await lab.say("actually I spent 20 at Lidl")
    amounts = sorted(
        a
        for (a,) in await lab.rows(select(Expense.amount).where(Expense.member_id == lab.member_id))
    )
    assert amounts == [10.0, 20.0]  # was: the Jumbo expense was rewritten to 20


async def test_amount_correction_out_of_range_does_not_crash_or_reply_twice(lab: Lab) -> None:
    from alfred.models import Expense

    lab.expense.return_value = {
        "amount": 10.0,
        "currency": "EUR",
        "merchant": "Jumbo",
        "category": "supermarkt",
        "description": "x",
        "type": "expense",
        "days_ago": 0,
    }
    await lab.say("gastei 10 no jumbo")
    lab.expense.return_value = None
    await lab.say("errei foram 99999999999")  # exactly one reply, no NumericValueOutOfRange
    amount = await lab.scalar(select(Expense.amount).where(Expense.member_id == lab.member_id))
    assert amount == 10.0


async def test_emoji_before_a_command_does_not_truncate_the_captured_text(lab: Lab) -> None:
    await lab.add(Task(member_id=lab.member_id, body="ligar ao pai"))
    reply = await lab.say("❤️ apaga tarefa ligar ao pai")
    # regex is anchored at the start, so an emoji prefix means "not a command" — but it must
    # never delete a *different* task via a truncated key
    assert await lab.scalar(select(func.count()).where(Task.member_id == lab.member_id)) == 1
    assert reply


async def test_cents_do_not_show_float_noise(lab: Lab) -> None:
    from alfred.models import Expense

    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            amount=0.30,
            transaction_type="income",
            category="inkomen",
        ),
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            amount=0.10,
            transaction_type="expense",
            category="overig",
        ),
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            amount=0.20,
            transaction_type="expense",
            category="overig",
        ),
    )
    reply = await lab.say("saldo")
    assert "-€0,00" not in reply and "€0,00" in reply
