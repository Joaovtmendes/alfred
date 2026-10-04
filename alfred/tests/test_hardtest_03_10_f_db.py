"""Hard test 03/10, group F — chat (defects 20, 22)."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from alfred.budgets import project_month_end
from alfred.clock import today_local
from alfred.models import Task
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.mark.parametrize(("day", "shown"), [(3, False), (9, False), (10, True), (28, True)])
def test_the_chat_projection_waits_for_enough_days_like_the_panel(day: int, shown: bool):
    """Defect 20: on day 3 the chat projected 1.327 EUR for the month; the panel said "not enough
    data"."""
    got = project_month_end(Decimal("128"), date(2026, 10, day))
    assert (got is not None) is shown


@db
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "title", "weekday"),
    [
        ("tarefa: enviar relatório até sexta", "enviar relatório", 4),
        ("tarefa: revisar contrato até segunda-feira", "revisar contrato", 0),
        ("task: send report by friday", "send report", 4),
    ],
)
async def test_a_task_deadline_in_words_becomes_the_due_date(
    lab: Lab, text: str, title: str, weekday: int
):
    """Defect 22: "até sexta" stayed in the title and the task had no due date."""
    await lab.say(text)
    row = (await lab.rows(select(Task.body, Task.due_date).where(Task.member_id == lab.member_id)))[
        0
    ]
    assert row[0].lower() == title
    assert row[1] is not None and row[1].weekday() == weekday and row[1] >= today_local()


@db
@pytest.mark.asyncio
async def test_until_something_that_is_not_a_date_stays_in_the_title(lab: Lab):
    await lab.say("tarefa: ligar para o João até logo")
    row = (await lab.rows(select(Task.body, Task.due_date).where(Task.member_id == lab.member_id)))[
        0
    ]
    assert row[0].lower() == "ligar para o joão até logo" and row[1] is None
