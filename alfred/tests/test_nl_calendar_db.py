# ruff: noqa: E501
"""V2-21 — Dutch calendar: official deadlines on request and opt-in reminders."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal
from alfred.models import CalendarOptIn, Message
from alfred.nl_calendar import (
    btw_deadline,
    due_reminders,
    events_due,
    health_deadline,
    mark_sent,
    tax_deadline,
)
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.conversation.today_local", lambda: TODAY)


# ── pure rules ────────────────────────────────────────────────────────────────


def test_health_deadline_is_31_december() -> None:
    assert health_deadline(date(2026, 10, 14)) == date(2026, 12, 31)
    assert health_deadline(date(2027, 1, 5)) == date(2027, 12, 31)


def test_tax_deadline_is_the_next_1_may() -> None:
    assert tax_deadline(date(2026, 10, 14)) == date(2027, 5, 1)
    assert tax_deadline(date(2027, 5, 1)) == date(2027, 5, 1)
    assert tax_deadline(date(2027, 5, 2)) == date(2028, 5, 1)
    assert tax_deadline(date(2027, 2, 1)) == date(2027, 5, 1)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2026, 10, 14), date(2026, 10, 31)),  # Q3 2026
        (date(2026, 10, 31), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2027, 1, 31)),  # Q4 2026
        (date(2027, 2, 1), date(2027, 4, 30)),  # Q1 2027
        (date(2027, 5, 15), date(2027, 7, 31)),  # Q2 2027
        (date(2027, 12, 20), date(2028, 1, 31)),
    ],
)
def test_btw_deadline_is_end_of_month_after_the_quarter(today, expected) -> None:
    assert btw_deadline(today) == expected


def test_events_by_date() -> None:
    assert events_due(date(2026, 10, 14)) == []
    (e,) = events_due(date(2026, 10, 25))
    assert e.key == "btw-2026-10-31" and e.stage == "btw"
    (e,) = events_due(date(2026, 11, 10))
    assert e.key == "health-2026-nov" and e.deadline == date(2026, 12, 31)
    assert events_due(date(2026, 12, 1)) == []
    (e,) = events_due(date(2026, 12, 20))
    assert e.key == "health-2026-dec"
    keys = {e.key for e in events_due(date(2027, 1, 25))}
    assert keys == {"btw-2027-01-31"}
    assert {x.key for x in events_due(date(2027, 4, 20))} == {"tax-2027", "btw-2027-04-30"}


# ── chat ──────────────────────────────────────────────────────────────────────


@db
@pytest.mark.parametrize("text", ["prazos", "Prazos!", "calendário holandês", "datas importantes"])
async def test_overview(lab: Lab, text) -> None:
    out = await lab.say(text)
    assert "31/12/2026" in out and "01/05/2027" in out and "31/10/2026" in out
    assert "Tikkie" in out and "ligar avisos de prazos" in out


@db
async def test_topic_questions(lab: Lab) -> None:
    out = await lab.say("quando posso trocar de seguro saúde?")
    assert "31/12/2026" in out and "1º de fevereiro" in out
    out = await lab.say("qual o prazo da declaração de imposto de renda")
    assert "01/05/2027" in out and "carta" in out
    out = await lab.say("prazo do btw")
    assert "31/10/2026" in out and "(faltam 17 dias)" in out


@db
async def test_tikkie_question(lab: Lab) -> None:
    out = await lab.say("qual o prazo do tikkie?")
    assert "Tikkie" in out and "não há prazo oficial" in out


@db
async def test_unrelated_text_falls_through(lab: Lab) -> None:
    assert await lab.say("quanto gastei com seguro?") != ""
    assert await lab.say("tenho que pagar o seguro saúde, gastei 140") != ""
    assert await lab.say("o prazo do projeto vence amanhã") == "[llm]"


@db
async def test_opt_in_and_out(lab: Lab) -> None:
    async def count() -> int:
        return await lab.scalar(select(func.count()).select_from(CalendarOptIn))

    assert "desligados" in await lab.say("avisos de prazos")
    assert "Pronto" in await lab.say("ligar avisos de prazos")
    assert await count() == 1
    await lab.say("ligar avisos de prazos")
    assert await count() == 1
    assert "ligados" in await lab.say("avisos de prazos")
    assert "não vou mais" in await lab.say("desligar avisos de prazos")
    assert await count() == 0


@db
async def test_other_languages(lab: Lab) -> None:
    for text in ("deadlines", "termijnen", "échéances", "fristen", "wichtige Termine"):
        assert "Prazos que costumam" in await lab.say(text)  # the lab member speaks pt
    assert "Seguro-saúde" in await lab.say("wanneer overstappen zorgverzekering")
    assert "31/10/2026" in await lab.say("when is the btw deadline")


@db
async def test_export_and_erase_cover_optin(lab: Lab) -> None:
    await lab.say("ligar avisos de prazos")
    async with AsyncSessionLocal() as s:
        from alfred.models import Member

        member = await s.get(Member, lab.member_id)
        assert "sent_keys" in str(await export_member_data(s, member))
        await erase_member(s, member)
        await s.commit()
    assert await lab.scalar(select(func.count()).select_from(CalendarOptIn)) == 0


# ── reminders ─────────────────────────────────────────────────────────────────


async def _opt_in(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        s.add(CalendarOptIn(member_id=lab.member_id, sent_keys=""))
        await s.commit()


@db
async def test_reminders_only_for_opted_in_and_once(lab: Lab) -> None:
    day = date(2026, 12, 20)
    async with AsyncSessionLocal() as s:
        assert await due_reminders(s, day, datetime.now(UTC)) == []  # not opted in
    await _opt_in(lab)
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, day, datetime.now(UTC))
        assert r.key == "health-2026-dec" and "11 dias" in r.text and "31/12/2026" in r.text
        await mark_sent(s, r.member_id, r.key)
        await s.commit()
        assert await due_reminders(s, day, datetime.now(UTC)) == []
        (r2,) = await due_reminders(s, date(2027, 1, 25), datetime.now(UTC))
        assert r2.key == "btw-2027-01-31"


@db
async def test_sent_keys_keep_only_the_last_12(lab: Lab) -> None:
    await _opt_in(lab)
    async with AsyncSessionLocal() as s:
        for i in range(15):
            await mark_sent(s, lab.member_id, f"k{i}")
            await s.commit()
        opt = (await s.execute(select(CalendarOptIn))).scalars().first()
        assert opt.sent_keys.split(",") == [f"k{i}" for i in range(3, 15)]


@db
async def test_in_window_flag_follows_last_inbound(lab: Lab) -> None:
    await _opt_in(lab)
    day = date(2026, 4, 20)
    async with AsyncSessionLocal() as s:
        rs = await due_reminders(s, day, datetime.now(UTC))
        assert {r.key for r in rs} == {"tax-2026", "btw-2026-04-30"}
        assert all(r.in_window is False for r in rs)
        s.add(
            Message(
                wa_message_id=uuid.uuid4().hex,
                household_id=lab.household_id,
                author_id=lab.member_id,
                direction="inbound",
                body="oi",
            )
        )
        await s.commit()
        assert all(r.in_window for r in await due_reminders(s, day, datetime.now(UTC)))
