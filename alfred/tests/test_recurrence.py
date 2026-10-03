"""V1-24 #1 — reminder cadence: weekdays, weekly, monthly day; text stripped of the phrase."""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from alfred.models import ScheduledJob
from alfred.recurrence import cadence_label, parse_recurrence
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.mark.parametrize(
    ("text", "clean", "mask", "dom"),
    [
        ("tomar vitamina toda segunda", "tomar vitamina", 1, None),
        ("regar as plantas todos os dias úteis", "regar as plantas", 31, None),
        ("pagar o aluguel todo dia 5", "pagar o aluguel", 127, 5),
        ("beber água todo dia", "beber água", 127, None),
        ("correr aos fins de semana", "correr", 96, None),
        ("take pills every monday", "take pills", 1, None),
        ("take pills on weekdays", "take pills", 31, None),
        ("pay rent on the 5th of every month", "pay rent", 127, 5),
        ("afval buiten zetten elke dinsdag", "afval buiten zetten", 2, None),
        ("ligar para a mãe", "ligar para a mãe", 127, None),
    ],
)
def test_parse_recurrence(text, clean, mask, dom):
    r = parse_recurrence(text)
    assert (r.text, r.mask, r.day_of_month) == (clean, mask, dom)


def test_cadence_label_languages():
    assert cadence_label(127, None, "pt") == "todo dia"
    assert cadence_label(31, None, "pt") == "de segunda a sexta"
    assert cadence_label(1, None, "pt") == "toda segunda"
    assert cadence_label(127, 5, "pt") == "todo dia 5 do mês"
    assert cadence_label(31, None, "en") == "every weekday"
    assert cadence_label(127, 5, "nl") == "elke maand op de 5e"


def test_cron_respects_day_of_month():
    from scripts.daily_cron import is_job_due

    tz = ZoneInfo("Europe/Amsterdam")
    on = datetime(2026, 10, 5, 10, 1, tzinfo=tz)
    off = datetime(2026, 10, 6, 10, 1, tzinfo=tz)
    assert is_job_due("10:00", 127, None, on, day_of_month=5)
    assert not is_job_due("10:00", 127, None, off, day_of_month=5)
    assert is_job_due("10:00", 127, None, off)


@db
@pytest.mark.asyncio
async def test_chat_reminders_store_right_cadence(lab: Lab):
    r1 = await lab.say("me lembra de regar as plantas todos os dias úteis às 9h")
    r2 = await lab.say("me lembra de tomar vitamina toda segunda às 8h")
    r3 = await lab.say("me lembra de pagar o aluguel todo dia 5 às 10h")
    rows = await lab.rows(
        select(ScheduledJob.days_mask, ScheduledJob.payload).order_by(ScheduledJob.time_of_day)
    )
    by_time = {p["text"]: (m, p.get("day_of_month")) for m, p in rows}
    assert by_time == {
        "tomar vitamina": (1, None),
        "regar as plantas": (31, None),
        "pagar o aluguel": (127, 5),
    }
    assert "de segunda a sexta" in r1
    assert "toda segunda" in r2
    assert "todo dia 5 do mês" in r3
    listing = await lab.say("os meus lembretes")
    assert "[daily]" not in listing
