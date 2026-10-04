"""Hard test 03/10, group C — reminders (defects 17, 10, 11)."""

from __future__ import annotations

import pytest

from alfred.recurrence import parse_recurrence
from tests.test_panel_tabs_api import _cards, db


@pytest.mark.parametrize(
    ("text", "title", "mask"),
    [
        ("pagar renda toda a segunda", "pagar renda", 1),
        ("pagar renda toda a segunda-feira", "pagar renda", 1),
        ("ligar para a mãe todas as sextas", "ligar para a mãe", 16),
        ("pagar renda toda segunda", "pagar renda", 1),
    ],
)
def test_toda_a_segunda_is_a_weekly_cadence(text: str, title: str, mask: int):
    """Defect 17: the reminder was saved as "pagar renda toda a"."""
    rec = parse_recurrence(text)
    assert (rec.text, rec.mask) == (title, mask)


@db
@pytest.mark.asyncio
async def test_the_panel_names_reminders_by_what_they_are(lab, client):
    """Defect 10: every reminder was labelled "Medication"; defect 11: "todo dia 5" was daily."""
    from alfred.models import ScheduledJob

    await lab.add(
        ScheduledJob(
            member_id=lab.member_id,
            job_type="medication_reminder",
            time_of_day="10:00",
            days_mask=1,
            payload={"text": "pagar renda"},
        ),
        ScheduledJob(
            member_id=lab.member_id,
            job_type="medication_reminder",
            time_of_day="09:00",
            days_mask=127,
            payload={"text": "pagar o aluguel", "day_of_month": 5},
        ),
        ScheduledJob(
            member_id=lab.member_id,
            job_type="medication_reminder",
            time_of_day="08:00",
            days_mask=127,
            payload={"text": "tomar o medicamento"},
        ),
    )
    items = {i["text"]: i for i in (await _cards(client, lab, "agenda"))["reminders"]["items"]}
    assert items["pagar renda"]["kind"] == "reminder"
    assert items["tomar o medicamento"]["kind"] == "medication_reminder"
    rent = items["pagar o aluguel"]
    assert rent["kind"] == "reminder" and rent["day_of_month"] == 5 and not rent["every_day"]
    assert items["pagar renda"]["day_of_month"] is None


# ── group D — goals (defect 12) ──────────────────────────────────────────────


def test_a_habit_verb_finds_its_goal_by_stem():
    from alfred.conversation import _goal_for_activity

    class G:
        def __init__(self, title: str):
            self.title = title

    meditate, read, save = G("meditar todos os dias"), G("ler 20 páginas"), G("juntar 5000€")
    goals = [save, read, meditate]
    assert _goal_for_activity(goals, "meditei") is meditate
    assert _goal_for_activity(goals, "meditated") is meditate  # same stem in English
    assert _goal_for_activity(goals, "ler hoje") is read
    assert _goal_for_activity(goals, "fiz yoga") is None
    assert _goal_for_activity(goals, "") is None


@db
@pytest.mark.asyncio
async def test_meditei_counts_for_the_goal_and_a_savings_goal_has_no_day_bar(lab, client):
    from alfred.models import Goal

    await lab.add(
        Goal(member_id=lab.member_id, title="meditar todos os dias"),
        Goal(
            member_id=lab.member_id,
            title="juntar 5000€",
            target_value="5000",
            target_unit="EUR",
        ),
    )
    await lab.say("meditei hoje")
    items = {i["title"]: i for i in (await _cards(client, lab, "health"))["goals"]["items"]}
    assert items["meditar todos os dias"]["logs_7d"] == 1
    assert items["meditar todos os dias"]["money"] is False
    assert items["juntar 5000€"]["money"] is True
