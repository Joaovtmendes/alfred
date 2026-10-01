"""V2-04 — monthly summary: content, empty months, opt-out, idempotent cron delivery."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from alfred.clock import local_tz, now_local, prev_month_start, today_local
from alfred.db import AsyncSessionLocal
from alfred.models import (
    Budget,
    Expense,
    HabitLog,
    Member,
    Message,
    RecurringItem,
    ScheduledJob,
    WorkoutSession,
)
from alfred.monthly_summary import STRINGS, build_summary, mark_sent, pending_summaries
from alfred.settings import settings
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

FIRST = prev_month_start(today_local())  # the month the summary is about
PREV = prev_month_start(FIRST)
# the cron sends on the 1st: pretend it is 10:00 on day 1 of this month
DAY1 = datetime.combine(
    date.today().replace(day=1), datetime.min.time(), tzinfo=local_tz()
).replace(hour=10)


def _at(first: date, day: int = 10) -> datetime:
    return datetime(first.year, first.month, day, 12, tzinfo=local_tz())


def _txn(lab: Lab, amount: float, category: str, first: date, kind: str = "expense") -> Expense:
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        currency="EUR",
        merchant="x",
        category=category,
        expense_date=_at(first),
    )


async def _summary(lab: Lab, lang: str = "pt", **kw) -> str | None:
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        return await build_summary(s, member, lang, FIRST, **kw)


@db
async def test_summary_has_totals_top_categories_and_change(lab: Lab) -> None:
    await lab.add(
        _txn(lab, 3000, "inkomen", FIRST, "income"),
        _txn(lab, 400, "supermarkt", FIRST),
        _txn(lab, 250, "restaurant", FIRST),
        _txn(lab, 100, "transport", FIRST),
        _txn(lab, 50, "kleding", FIRST),
        _txn(lab, 400, "supermarkt", PREV),  # prior month: 800 → 400 is +100 %... see below
    )
    text = await _summary(lab)
    assert "Receitas €3.000,00" in text and "Despesas €800,00" in text and "Saldo €2.200,00" in text
    assert "Supermercado €400,00" in text and "Restaurante €250,00" in text
    assert "Vestuário" not in text  # only the top three
    assert "+100%" in text  # 800 vs 400
    assert "meu dashboard" in text


@db
async def test_income_only_month_has_no_division_by_zero_and_no_comparison(lab: Lab) -> None:
    await lab.add(_txn(lab, 1000, "inkomen", FIRST, "income"))
    text = await _summary(lab)
    assert "Despesas €0,00" in text and "em relação a" not in text


@db
async def test_empty_month_is_a_short_nudge_without_zero_numbers(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", PREV))  # has history, but nothing last month
    text = await _summary(lab)
    assert "não vi lançamentos" in text and "€0" not in text and "Receitas" not in text


@db
async def test_member_with_no_history_gets_nothing(lab: Lab) -> None:
    assert await _summary(lab) is None


@db
async def test_budgets_bills_workouts_and_habits_lines(lab: Lab) -> None:
    await lab.add(
        _txn(lab, 150, "supermarkt", FIRST),
        _txn(lab, 90, "restaurant", FIRST),
        Budget(
            member_id=lab.member_id,
            household_id=lab.household_id,
            category="supermarkt",
            monthly_limit=200,
        ),
        Budget(
            member_id=lab.member_id,
            household_id=lab.household_id,
            category="restaurant",
            monthly_limit=50,
        ),
        RecurringItem(
            member_id=lab.member_id,
            household_id=lab.household_id,
            name="Aluguel",
            amount=1200,
            next_due_date=today_local(),
        ),
        WorkoutSession(
            member_id=lab.member_id, activity_type="running", workout_date=FIRST.replace(day=3)
        ),
        WorkoutSession(
            member_id=lab.member_id, activity_type="yoga", workout_date=FIRST.replace(day=5)
        ),
        HabitLog(member_id=lab.member_id, activity="meditar", log_date=FIRST.replace(day=4)),
    )
    text = await _summary(lab)
    assert "dentro do limite 1, estourado: Restaurante" in text
    assert "Contas fixas: 1, €1.200,00 por mês" in text
    assert "Treinos: 2 · Hábitos registrados: 1" in text


@db
async def test_one_line_version_for_templates_has_no_newlines(lab: Lab) -> None:
    await lab.add(_txn(lab, 100, "supermarkt", FIRST))
    line = await _summary(lab, one_line=True)
    assert "\n" not in line and line.startswith("Resumo de")


@db
async def test_other_months_and_other_members_do_not_leak_in(lab: Lab) -> None:
    await lab.add(
        _txn(lab, 77, "supermarkt", FIRST),
        _txn(lab, 999, "supermarkt", today_local().replace(day=1)),
    )
    text = await _summary(lab)
    assert "€77,00" in text and "999" not in text


@db
async def test_summary_in_every_language(lab: Lab) -> None:
    await lab.add(_txn(lab, 100, "supermarkt", FIRST))
    for lang in ("pt", "nl", "en", "fr", "de"):
        text = await _summary(lab, lang)
        assert "100,00" in text, lang
    for key in STRINGS:
        assert set(STRINGS[key]) >= {"pt", "nl", "en", "fr", "de"}, key


# ── commands ──────────────────────────────────────────────────────────────────


@db
async def test_ask_for_it_now(lab: Lab) -> None:
    assert "ainda não tenho lançamentos" in (await lab.say("resumo mensal")).lower()
    await lab.add(_txn(lab, 100, "supermarkt", FIRST))
    assert "Despesas €100,00" in await lab.say("resumo mensal")


@db
async def test_opt_out_and_back_in(lab: Lab) -> None:
    assert "não envio mais" in await lab.say("sem resumo mensal")
    job = await lab.scalar(select(ScheduledJob).where(ScheduledJob.member_id == lab.member_id))
    assert job.job_type == "monthly_summary" and job.active is False
    assert "todo dia 1" in await lab.say("ativar resumo mensal")
    await lab.say("sem resumo mensal")  # a second time updates the same row
    rows = await lab.rows(select(ScheduledJob).where(ScheduledJob.member_id == lab.member_id))
    assert len(rows) == 1


@db
async def test_the_bookkeeping_row_is_not_listed_as_a_reminder(lab: Lab) -> None:
    await lab.say("ativar resumo mensal")
    reply = await lab.say("meus lembretes")
    assert "monthly_summary" not in reply


# ── cron ──────────────────────────────────────────────────────────────────────


async def _mine(lab: Lab, now=DAY1):
    async with AsyncSessionLocal() as s:
        return [d for d in await pending_summaries(s, now) if d.wa_phone == lab.phone]


@db
async def test_due_only_on_days_1_to_3_in_waking_hours(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    assert len(await _mine(lab)) == 1
    assert await _mine(lab, DAY1.replace(day=4)) == []
    assert await _mine(lab, DAY1.replace(hour=8)) == []
    assert await _mine(lab, DAY1.replace(hour=21)) == []
    assert len(await _mine(lab, DAY1.replace(day=3))) == 1


@db
async def test_once_per_month_and_opt_out_respected(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    async with AsyncSessionLocal() as s:
        await mark_sent(s, lab.member_id, DAY1)
        await s.commit()
    assert await _mine(lab) == []
    await lab.say("ativar resumo mensal")
    assert await _mine(lab) == []  # still counts as sent this month
    async with AsyncSessionLocal() as s:  # last month's send does not block this month
        job = (
            await s.execute(select(ScheduledJob).where(ScheduledJob.member_id == lab.member_id))
        ).scalar_one()
        job.last_sent_at = DAY1 - timedelta(days=31)
        await s.commit()
    assert len(await _mine(lab)) == 1
    await lab.say("sem resumo mensal")
    assert await _mine(lab) == []


@db
async def test_non_consenting_member_gets_nothing(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    async with AsyncSessionLocal() as s:
        (await s.get(Member, lab.member_id)).consent_state = "rejected"
        await s.commit()
    assert await _mine(lab) == []


def _cron():
    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent / "scripts" / "daily_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


@db
async def test_cron_sends_in_window_once(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    await _inbound(lab, 1)
    sent: list[tuple[str, str]] = []

    async def fake_text(to, text):
        sent.append((to, text))

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_monthly_summaries(DAY1)
        await _cron().send_monthly_summaries(DAY1)
    mine = [t for to, t in sent if to == lab.phone]
    assert len(mine) == 1 and "Resumo de" in mine[0]


@db
async def test_cron_outside_window_uses_the_template_only_when_enabled(
    lab: Lab, monkeypatch
) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    calls: list[dict] = []

    async def fake_template(**kw):
        calls.append(kw)

    monkeypatch.setattr(settings, "monthly_summary_template_enabled", False)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_monthly_summaries(DAY1)
    assert [c for c in calls if c["to"] == lab.phone] == []
    assert len(await _mine(lab)) == 1  # still owed

    monkeypatch.setattr(settings, "monthly_summary_template_enabled", True)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_monthly_summaries(DAY1)
    mine = [c for c in calls if c["to"] == lab.phone]
    assert len(mine) == 1 and mine[0]["template_name"] == "alfred_monthly_summary"
    param = mine[0]["components"][0]["parameters"][0]["text"]
    assert "\n" not in param
    assert await _mine(lab) == []


@db
async def test_failed_send_is_retried_and_does_not_block_others(lab: Lab) -> None:
    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    await _inbound(lab, 1)

    async def boom(to, text):
        raise RuntimeError("meta down")

    with patch("alfred.whatsapp.send_text", boom):
        _, errors = await _cron().send_monthly_summaries(DAY1)
    assert errors >= 1
    assert len(await _mine(lab)) == 1


@db
async def test_year_turn_summarises_december(lab: Lab) -> None:
    jan1 = datetime(2027, 1, 1, 10, tzinfo=local_tz())
    assert prev_month_start(jan1.date()) == date(2026, 12, 1)
    dec = date(2026, 12, 1)
    await lab.add(_txn(lab, 55, "supermarkt", dec))
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        text = await build_summary(s, member, "pt", dec)
    assert "Dezembro" in text and "€55,00" in text
