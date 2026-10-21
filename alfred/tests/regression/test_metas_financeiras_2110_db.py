# ruff: noqa: E501
"""B-073: metas financeiras têm parte própria (aportes) e metas de rotina mostram os dias da semana."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from alfred.models import Goal, GoalContribution
from tests.labkit import Lab
from tests.test_panel_tabs_api import _cards

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _goals(lab: Lab) -> list[Goal]:
    return [r[0] for r in await lab.rows(select(Goal).where(Goal.member_id == lab.member_id))]


@db
@pytest.mark.bug("B-073")
async def test_a_money_goal_takes_target_and_deadline_from_its_words(lab: Lab) -> None:
    out = await lab.say("meta: reserva de emergência 5000 até dezembro")
    (goal,) = await _goals(lab)
    assert goal.target_value == "5000" and goal.target_unit == "EUR"
    assert goal.deadline is not None and goal.deadline.month == 12
    assert "guardei" in out  # tells how to add the money put aside


@db
@pytest.mark.bug("B-073")
async def test_contributions_add_set_and_list(lab: Lab) -> None:
    await lab.say("meta: reserva de emergência 5000")
    out = await lab.say("guardei 500 na reserva de emergência")
    assert "500,00" in out and "10%" in out
    out = await lab.say("guardei 250 na reserva")
    assert "750,00" in out and "15%" in out
    out = await lab.say("tenho 2000 na reserva de emergência")
    assert "2.000,00" in out and "40%" in out
    kinds = [r[0].kind for r in await lab.rows(select(GoalContribution))]
    assert kinds.count("add") == 2 and kinds.count("set") == 1
    listing = await lab.say("metas financeiras")
    assert "reserva" in listing.lower() and "2.000,00" in listing


@db
@pytest.mark.bug("B-073")
async def test_saving_words_without_a_financial_goal_are_a_normal_message(lab: Lab) -> None:
    lab.multi.return_value = []
    await lab.say("guardei 20 no cofre")
    assert not [r for r in await lab.rows(select(GoalContribution))]


@db
@pytest.mark.bug("B-073")
async def test_a_routine_goal_shows_the_week_marks_after_a_check_in(lab: Lab) -> None:
    await lab.say("meta: meditar todos os dias")
    out = await lab.say("meditei hoje")
    assert "✅" in out and "1 de 7" in out


@db
@pytest.mark.bug("B-073")
async def test_the_panel_splits_habit_goals_from_money_goals(lab: Lab, client) -> None:
    await lab.say("meta: meditar todos os dias")
    await lab.say("meta: reserva de emergência 5000")
    await lab.say("guardei 1000 na reserva")
    await lab.say("meditei hoje")
    health = await _cards(client, lab, "health")
    assert [i["title"] for i in health["goals"]["items"]] == ["meditar todos os dias"]
    week = health["goals"]["items"][0]["week"]
    assert len(week) == 7 and week.count("done") == 1
    money = await _cards(client, lab, "money")
    item = money["money_goals"]["items"][0]
    assert item["saved"] == 1000.0 and item["saved_pct"] == 20 and item["target_amount"] == 5000.0
    assert money["money_goals"]["values"]["saved_total"] == 1000.0


@db
@pytest.mark.bug("B-073")
async def test_the_money_goals_card_is_empty_with_a_hint_for_a_new_member(lab: Lab, client) -> None:
    card = (await _cards(client, lab, "money"))["money_goals"]
    assert card["empty"] is True and card["hint"]["key"] == "empty_money_goals"
