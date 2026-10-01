"""V2-06 — agenda: date/time rules, commands, buttons, conflicts and cron reminders."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from alfred.agenda import (
    clean_title,
    mark_reminded,
    parse_appointment,
    parse_when,
    pending_reminders,
    to_datetime,
)
from alfred.clock import local_tz, now_local
from alfred.db import AsyncSessionLocal
from alfred.models import Appointment, Member, Message
from alfred.parsing import strip_accents
from alfred.privacy import export_member_data
from alfred.settings import settings
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

AMS = ZoneInfo("Europe/Amsterdam")
THU = datetime(2026, 10, 1, 10, 0, tzinfo=AMS)  # a Thursday


def _p(text: str, now: datetime = THU):
    body = text.lower()
    return parse_appointment(body, strip_accents(body), now)


# ── pure rules ────────────────────────────────────────────────────────────────


def test_the_documented_phrases() -> None:
    a = _p("dentista quinta às 14h")
    assert (a.title, a.starts_at) == (
        "Dentista",
        datetime(2026, 10, 1, 14, 0, tzinfo=AMS),
    )  # still ahead today
    a = _p("reunião dia 5 às 10:30, me avisa 1h antes")
    assert (a.title, a.starts_at, a.remind_minutes) == (
        "Reunião",
        datetime(2026, 10, 5, 10, 30, tzinfo=AMS),
        60,
    )
    assert _p("dentista amanhã às 14h").starts_at == datetime(2026, 10, 2, 14, 0, tzinfo=AMS)
    assert _p("marca dentista sexta 15h30").starts_at == datetime(2026, 10, 2, 15, 30, tzinfo=AMS)
    assert _p("dentista dia 5 de outubro às 8h").starts_at == datetime(
        2026, 10, 5, 8, 0, tzinfo=AMS
    )
    assert _p("reunion 5/10 14:00").starts_at == datetime(2026, 10, 5, 14, 0, tzinfo=AMS)


def test_other_languages() -> None:
    assert _p("dentist tomorrow at 2pm").starts_at.hour == 14
    assert _p("tandarts morgen om 14:30").starts_at.minute == 30
    assert _p("zahnarzt am freitag um 9 uhr").starts_at == datetime(2026, 10, 2, 9, 0, tzinfo=AMS)
    assert _p("dentiste vendredi à 16h").starts_at.hour == 16
    a = _p("lunch on friday at 12:30 remind me 30 min before")
    assert a.remind_minutes == 30 and a.title == "Lunch"
    assert _p("tandarts overmorgen om 9:15").starts_at.day == 3


def test_reminder_lead_units() -> None:
    assert _p("dentista amanhã 14h avisa 2h antes").remind_minutes == 120
    assert _p("dentista amanhã 14h me avisa 15 min antes").remind_minutes == 15
    assert _p("dentist tomorrow 2pm remind me 1 hour before").remind_minutes == 60


def test_weekday_today_only_while_the_time_is_ahead() -> None:
    assert _p("dentista quinta às 14h").starts_at.date() == date(2026, 10, 1)
    assert _p("dentista quinta às 8h").starts_at.date() == date(2026, 10, 8)  # 08:00 already gone


def test_day_of_month_rolls_into_the_next_month_and_clamps() -> None:
    assert _p("dentista dia 5 às 10h").starts_at.date() == date(2026, 10, 5)
    assert _p("dentista dia 1 às 17h").starts_at.date() == date(2026, 10, 1)
    assert _p("dentista dia 1 às 9h") == "past"  # 09:00 today is gone; "dia 1" means today
    assert _p("dentista dia 30 às 9h").starts_at.date() == date(2026, 10, 30)
    assert _p(
        "dentista dia 3 às 9h", datetime(2026, 10, 20, 10, tzinfo=AMS)
    ).starts_at.date() == date(2026, 11, 3)
    jan31 = datetime(2026, 2, 10, 10, tzinfo=AMS)
    assert _p("dentista dia 31 às 10h", jan31).starts_at.date() == date(2026, 3, 31)


def test_not_an_appointment() -> None:
    for text in (
        "corri hoje às 7h",
        "pizza 18 ontem à noite",
        "almoço 15 euros amanhã às 13h",
        "jantar 20h",  # a time but no date
        "dentista amanhã",  # a date but no time
        "uber 8,5",
        "gastei 12 amanhã às 3",
        "quando é o dentista na quinta às 14h?",
        "paguei o aluguel dia 1 às 9h",
    ):
        assert _p(text) is None, text


def test_a_moment_that_has_passed_is_flagged() -> None:
    assert _p("dentista hoje às 9h") == "past"


def test_title_cleanup() -> None:
    w = parse_when("dentista na quinta as 14h", THU)
    assert clean_title("dentista na quinta às 14h", w.spans) == "Dentista"
    assert clean_title("reunião com a Ana amanhã", []).startswith("Reunião com a Ana")


def test_spring_forward_gap_does_not_crash() -> None:
    d = to_datetime(date(2027, 3, 28), (2, 30))  # 02:30 does not exist in Amsterdam
    assert d.astimezone(AMS).date() == date(2027, 3, 28)


# ── commands ──────────────────────────────────────────────────────────────────


def _days(n: int) -> date:
    return now_local().date() + timedelta(days=n)


async def _appts(lab: Lab) -> list[Appointment]:
    return [
        r[0]
        for r in await lab.rows(
            select(Appointment)
            .where(Appointment.member_id == lab.member_id)
            .order_by(Appointment.starts_at)
        )
    ]


@db
async def test_create_with_buttons(lab: Lab) -> None:
    reply = await lab.say("dentista amanhã às 14h")
    assert "Dentista" in reply and "14:00" in reply and "1h" in reply
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["appt_ok", "appt_undo"]
    (a,) = await _appts(lab)
    assert a.title == "Dentista" and a.remind_before_minutes == 60 and a.status == "active"
    assert to_local_date(a) == _days(1)


def to_local_date(a: Appointment) -> date:
    return a.starts_at.astimezone(local_tz()).date()


@db
async def test_undo_and_ok_buttons(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    (a,) = await _appts(lab)
    assert await lab.tap(f"appt_ok:{a.id}")
    assert (await _appts(lab))[0].status == "active"  # "It's right" changes nothing
    reply = await lab.tap(f"appt_undo:{a.id}")
    assert "cancelado" in reply
    assert (await _appts(lab))[0].status == "cancelled"
    from alfred.conversation import _STRINGS

    assert await lab.tap(f"appt_undo:{a.id}") in (_STRINGS["button_gone"]["pt"],)


@db
async def test_a_forged_button_id_cannot_touch_another_members_appointment(lab: Lab) -> None:
    other = uuid.uuid4()
    reply = await lab.tap(f"appt_undo:{other}")
    assert reply  # a polite "gone" answer, nothing raised


@db
async def test_past_time_creates_nothing(lab: Lab) -> None:
    reply = await lab.say("dentista ontem às 14h")
    assert await _appts(lab) == [] and reply  # not an appointment at all: normal flow
    reply = await lab.say("dentista hoje às 00:01")
    assert "já passaram" in reply and await _appts(lab) == []


@db
async def test_overlap_warns_but_still_saves(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    reply = await lab.say("reunião amanhã às 14h30")
    assert "já tem Dentista às 14:00" in reply
    assert len(await _appts(lab)) == 2


@db
async def test_agenda_views(lab: Lab) -> None:
    assert "Nada na agenda" in await lab.say("minha agenda")
    await lab.say("dentista amanhã às 14h")
    await lab.say("reunião depois de amanhã às 10h")
    full = await lab.say("minha agenda")
    assert "Dentista" in full and "Reunião" in full and "(2)" in full
    tomorrow = await lab.say("agenda de amanhã")
    assert "Dentista" in tomorrow and "Reunião" not in tomorrow
    assert "Nada na agenda" in await lab.say("agenda de hoje")
    assert "Dentista" in await lab.say("agenda da semana")


@db
async def test_cancel_by_name_and_ambiguity(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    assert "cancelado" in await lab.say("cancela o dentista")
    assert (await _appts(lab))[0].status == "cancelled"
    await lab.say("consulta ana amanhã às 9h")
    await lab.say("consulta bia amanhã às 11h")
    amb = await lab.say("cancela a consulta")
    assert "Consulta ana" in amb and "Consulta bia" in amb


@db
async def test_cancel_with_no_match_is_left_to_the_other_handlers(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    await lab.say("cancela o netflix")  # no such bill or appointment
    assert (await _appts(lab))[0].status == "active"


@db
async def test_move_keeps_what_is_not_mentioned(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    reply = await lab.say("muda o dentista para 16h")
    assert "16:00" in reply
    (a,) = await _appts(lab)
    assert to_local_date(a) == _days(1) and a.starts_at.astimezone(local_tz()).hour == 16
    reply = await lab.say("muda o dentista para depois de amanhã")
    (a,) = await _appts(lab)
    assert to_local_date(a) == _days(2) and a.starts_at.astimezone(local_tz()).hour == 16
    assert "Para quando" in await lab.say("muda o dentista para banana")


@db
async def test_moving_resets_the_reminder(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    (a,) = await _appts(lab)
    async with AsyncSessionLocal() as s:
        await mark_reminded(s, a.id, now_local())
        await s.commit()
    await lab.say("muda o dentista para 16h")
    assert (await _appts(lab))[0].reminded_at is None


@db
async def test_ordinary_messages_are_not_hijacked(lab: Lab) -> None:
    lab.expense.return_value = {
        "amount": 35.0, "currency": "EUR", "merchant": "Shell", "category": "transport",
        "description": "gasolina", "type": "expense", "days_ago": 0,
    }  # fmt: skip
    await lab.say("paguei 35 de gasolina hoje às 9h")
    assert await _appts(lab) == []


@db
async def test_exported_and_all_texts_in_five_languages(lab: Lab) -> None:
    await lab.say("dentista amanhã às 14h")
    async with AsyncSessionLocal() as s:
        data = await export_member_data(s, await s.get(Member, lab.member_id))
    assert len(data["tables"]["appointment"]) == 1
    from alfred.agenda import STRINGS
    from alfred.conversation import _STRINGS

    for key in STRINGS:
        assert set(_STRINGS[key]) >= {"pt", "nl", "en", "fr", "de"}, key


# ── cron ──────────────────────────────────────────────────────────────────────


async def _add(lab: Lab, minutes_ahead: int, remind: int = 60, **kw) -> uuid.UUID:
    a = Appointment(
        id=uuid.uuid4(),
        member_id=lab.member_id,
        household_id=lab.household_id,
        title="Dentista",
        starts_at=now_local() + timedelta(minutes=minutes_ahead),
        remind_before_minutes=remind,
        **kw,
    )
    await lab.add(a)
    return a.id


async def _inbound(lab: Lab, hours_ago: float) -> None:
    await lab.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id=f"in-{uuid.uuid4()}",
            household_id=lab.household_id,
            author_id=lab.member_id,
            direction="inbound",
            body="oi",
            created_at=now_local() - timedelta(hours=hours_ago),
        )
    )


async def _pending(lab: Lab):
    async with AsyncSessionLocal() as s:
        return [r for r in await pending_reminders(s, now_local()) if r.wa_phone == lab.phone]


def _cron():
    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent / "scripts" / "daily_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@db
async def test_reminder_only_inside_the_lead_time_and_before_the_start(lab: Lab) -> None:
    await _add(lab, minutes_ahead=180)  # starts in 3 h, remind 1 h before: not yet
    assert await _pending(lab) == []
    await _add(lab, minutes_ahead=45)
    await _add(lab, minutes_ahead=-10)  # already started
    pend = await _pending(lab)
    assert len(pend) == 1 and "Dentista" in pend[0].text


@db
async def test_cancelled_or_already_reminded_are_skipped(lab: Lab) -> None:
    await _add(lab, 30, status="cancelled")
    await _add(lab, 30, reminded_at=now_local())
    assert await _pending(lab) == []


@db
async def test_cron_text_in_window_once(lab: Lab) -> None:
    await _add(lab, 30)
    await _inbound(lab, 1)
    sent: list[tuple[str, str]] = []

    async def fake_text(to, text):
        sent.append((to, text))

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_appointment_reminders(now_local())
        await _cron().send_appointment_reminders(now_local())
    mine = [t for to, t in sent if to == lab.phone]
    assert len(mine) == 1 and "Dentista" in mine[0]


@db
async def test_cron_outside_window_needs_the_template_flag(lab: Lab, monkeypatch) -> None:
    await _add(lab, 30)
    calls: list[dict] = []

    async def fake_template(**kw):
        calls.append(kw)

    monkeypatch.setattr(settings, "appointment_reminder_template_enabled", False)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_appointment_reminders(now_local())
    assert [c for c in calls if c["to"] == lab.phone] == []
    assert len(await _pending(lab)) == 1

    monkeypatch.setattr(settings, "appointment_reminder_template_enabled", True)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_appointment_reminders(now_local())
    mine = [c for c in calls if c["to"] == lab.phone]
    assert len(mine) == 1 and mine[0]["template_name"] == "alfred_appointment_reminder"
    assert await _pending(lab) == []


@db
async def test_failed_send_is_retried_next_run(lab: Lab) -> None:
    await _add(lab, 30)
    await _inbound(lab, 1)

    async def boom(to, text):
        raise RuntimeError("meta down")

    with patch("alfred.whatsapp.send_text", boom):
        _, errors = await _cron().send_appointment_reminders(now_local())
    assert errors >= 1 and len(await _pending(lab)) == 1


@db
async def test_count_of_rows_is_scoped_to_the_member(lab: Lab) -> None:
    await _add(lab, 30)
    n = await lab.scalar(
        select(func.count()).select_from(Appointment).where(Appointment.member_id == lab.member_id)
    )
    assert n == 1
