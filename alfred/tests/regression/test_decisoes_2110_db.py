# ruff: noqa: E501
"""Decisions of 21/10 on the open registry bugs: A-67 (button names) and A-74 (lone words)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred.batch import STRINGS as BATCH
from alfred.conversation import _STRINGS
from alfred.models import HealthLog, PendingAction
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.mark.bug("A-67")
def test_the_draft_button_says_cancel_and_the_saved_one_says_undo() -> None:
    assert BATCH["batch_btn_cancel"]["pt"] == "Cancelar"
    assert _STRINGS["btn_undo"]["pt"] == "Desfazer"
    for lang in ("pt", "nl", "en", "fr", "de"):
        assert BATCH["batch_btn_cancel"][lang] != _STRINGS["btn_undo"][lang]
        assert len(BATCH["batch_btn_cancel"][lang]) <= 20 and len(_STRINGS["btn_undo"][lang]) <= 20


@pytest.mark.bug("A-67")
def test_the_missing_entry_message_does_not_claim_it_was_deleted_by_you() -> None:
    assert "já foi apagado ou desfeito" in _STRINGS["button_gone"]["pt"]


@db
@pytest.mark.bug("A-74")
async def test_a_lone_water_word_asks_before_anything_is_recorded(lab: Lab) -> None:
    out = await lab.say("água")
    assert "sozinha eu não registro" in out
    assert [t for _, t in lab.buttons[-1]] == ["Sim, anotar", "Não"]
    n = await lab.scalar(
        select(func.count()).select_from(HealthLog).where(HealthLog.member_id == lab.member_id)
    )
    assert n == 0


@db
@pytest.mark.bug("A-74")
async def test_no_drops_the_question_and_yes_runs_the_phrase_through_the_normal_flow(
    lab: Lab,
) -> None:
    await lab.say("treino")
    no_id = lab.buttons[-1][1][0]
    assert "não anotei nada" in await lab.tap(no_id)
    left = await lab.scalar(
        select(func.count()).select_from(PendingAction).where(PendingAction.kind == "bare_word")
    )
    assert left == 0

    await lab.say("remédio")
    yes_id = lab.buttons[-1][0][0]
    lab.health.return_value = None
    await lab.tap(yes_id)
    assert "tomei o remédio" in str(lab.health.call_args)
    assert "não registro" not in lab.sent[-1]


@db
@pytest.mark.bug("A-74")
async def test_read_only_words_still_answer_directly(lab: Lab) -> None:
    assert "sozinha" not in await lab.say("agenda")
    assert "sozinha" not in await lab.say("saldo")


@pytest.mark.bug("A-58")
def test_the_panel_writes_money_like_the_chat() -> None:
    from alfred.conversation import _fmt_eur
    from alfred.panel_phrases import fmt_eur

    for value in (1234.5, 0, -133.6, 1234567.891):
        assert fmt_eur(value, "pt") == _fmt_eur(value)


# ── A-66: each person's own timezone ────────────────────────────────────────


def test_today_follows_the_timezone_of_the_person_being_served() -> None:

    from alfred.clock import now_local, use_member_timezone

    try:
        use_member_timezone("Pacific/Auckland")
        assert now_local().tzinfo.key == "Pacific/Auckland"
        use_member_timezone("not/a-zone")  # a bad name falls back to the server default
        assert now_local().tzinfo.key == "Europe/Amsterdam"
    finally:
        use_member_timezone(None)


@pytest.mark.bug("A-66")
def test_a_scheduled_job_fires_at_the_hour_where_the_member_lives() -> None:
    import importlib.util
    from datetime import UTC, datetime
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent.parent / "scripts" / "daily_cron.py"
    )
    cron = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cron)
    from zoneinfo import ZoneInfo

    now = datetime(2026, 10, 21, 9, 0, tzinfo=UTC).astimezone(ZoneInfo("Europe/Amsterdam"))  # 11:00
    lisbon = cron._in_member_zone(now, "Europe/Lisbon")  # 10:00 there
    assert (lisbon.hour, lisbon.minute) == (10, 0)
    assert cron._in_member_zone(now, None) is now
    assert cron._in_member_zone(now, "nope/zone") is now
    assert cron.is_job_due("10:00", 127, None, lisbon) and not cron.is_job_due(
        "10:00", 127, None, now
    )


@db
@pytest.mark.bug("A-66")
async def test_the_member_sets_a_timezone_by_city_or_name(lab: Lab) -> None:
    from alfred.models import Member

    out = await lab.say("fuso horário Lisboa")
    assert "Europe/Lisbon" in out
    assert (
        await lab.scalar(select(Member.timezone).where(Member.id == lab.member_id))
        == "Europe/Lisbon"
    )
    assert "America/Sao_Paulo" in await lab.say("fuso horario america/sao paulo")
    assert "Não reconheci" in await lab.say("fuso horário Marte")
    assert (
        await lab.scalar(select(Member.timezone).where(Member.id == lab.member_id))
        == "America/Sao_Paulo"
    )
    assert "America/Sao_Paulo" in await lab.say("meu fuso")


# ── F10-19: health consent is asked at the first medication, not stored before ───────────


@db
@pytest.mark.bug("F10-19")
async def test_the_first_medication_asks_for_health_consent_and_stores_nothing(lab: Lab) -> None:
    from sqlalchemy import update

    from alfred.db import AsyncSessionLocal
    from alfred.models import Member

    async with AsyncSessionLocal() as s:  # a member who did not tick health data at sign-up
        await s.execute(
            update(Member).where(Member.id == lab.member_id).values(health_consent_at=None)
        )
        await s.commit()
    lab.health.return_value = {
        "log_type": "medication", "value": "taken", "unit": None, "notes": "remédio",
        "days_ago": 0,
    }  # fmt: skip
    await lab.say("tomei o remédio")
    n = await lab.scalar(
        select(func.count()).select_from(HealthLog).where(HealthLog.member_id == lab.member_id)
    )
    assert n == 0
    assert lab.buttons[-1], "the consent question comes with its buttons"
