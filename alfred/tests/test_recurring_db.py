"""V2-02 — contas fixas: parsing, due dates, payments, instalments, reminders in the cron."""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local, today_local
from alfred.db import AsyncSessionLocal
from alfred.models import Expense, Member, Message, RecurringItem
from alfred.parsing import strip_accents
from alfred.privacy import export_member_data
from alfred.recurring import (
    advance,
    first_due,
    mark_reminded,
    monthly_equivalent,
    parse_recurring,
    pending_reminders,
)
from alfred.settings import settings
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _parse(text: str):
    body = text.lower()
    return parse_recurring(body, strip_accents(body))


# ── pure rules ────────────────────────────────────────────────────────────────


def test_parses_the_documented_phrases() -> None:
    p = _parse("aluguel 1200 todo dia 1")
    assert (p.name, p.amount, p.frequency, p.due_day, p.category) == (
        "Aluguel",
        1200.0,
        "monthly",
        1,
        "wonen",
    )
    p = _parse("netflix 13,99 mensal")
    assert (p.amount, p.kind, p.category, p.due_day) == (13.99, "subscription", "abonnement", None)
    p = _parse("celular em 10x de 89,90")
    assert (p.installments_total, p.amount, p.kind) == (10, 89.9, "installment")
    assert _parse("seguro saúde 145 mensal").name == "Seguro saúde"  # accents kept in the name
    assert _parse("rent 1200 monthly on the 1st").due_day == 1
    assert _parse("huur 950 elke maand").amount == 950
    assert _parse("spotify 10,99 jaarlijks").frequency == "yearly"
    assert _parse("gym 4 per week").frequency == "weekly"


def test_ordinary_expenses_are_not_recurring() -> None:
    for text in (
        "mercado 87,30 hoje",
        "netflix 13,99",
        "pizza 18 ontem à noite",
        "paguei 35 de gasolina",
        "uber 8,5",
    ):
        assert _parse(text) is None, text
    assert _parse("celular em 1x de 89,90") is None  # one instalment is just an expense
    assert _parse("aluguel 99999999 mensal") is None
    assert _parse("aluguel 1200 todo dia 40") is None


def test_first_due_and_month_end_clamping() -> None:
    today = date(2026, 10, 15)
    assert first_due("monthly", 20, today) == date(2026, 10, 20)  # still ahead this month
    assert first_due("monthly", 5, today) == date(2026, 11, 5)  # already passed
    assert first_due("monthly", None, today) == date(2026, 11, 15)  # one period from today
    assert first_due("monthly", 31, date(2026, 2, 1)) == date(2026, 2, 28)
    assert first_due("weekly", None, today) == date(2026, 10, 22)
    assert first_due("yearly", None, date(2024, 2, 29)) == date(2025, 2, 28)


def test_advance_keeps_the_original_day_after_a_short_month() -> None:
    d = date(2026, 1, 31)
    d = advance(d, "monthly", 31)
    assert d == date(2026, 2, 28)
    d = advance(d, "monthly", 31)
    assert d == date(2026, 3, 31)  # not stuck on the 28th
    assert advance(date(2026, 12, 15), "monthly", 15) == date(2027, 1, 15)  # year turn
    assert advance(date(2026, 10, 1), "weekly", None) == date(2026, 10, 8)


def test_monthly_equivalent() -> None:
    assert monthly_equivalent(100, "monthly") == Decimal("100.00")
    assert monthly_equivalent(120, "yearly") == Decimal("10.00")
    assert monthly_equivalent(10, "weekly") == Decimal("43.33")


# ── commands ──────────────────────────────────────────────────────────────────


async def _items(lab: Lab) -> list[RecurringItem]:
    rows = await lab.rows(select(RecurringItem).where(RecurringItem.member_id == lab.member_id))
    return [r[0] for r in rows]


async def _expenses(lab: Lab) -> int:
    return await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )


@db
async def test_create_and_list(lab: Lab) -> None:
    reply = await lab.say("aluguel 1200 todo dia 1")
    assert "Aluguel" in reply and "€1.200,00" in reply
    await lab.say("celular em 10x de 89,90")
    items = await _items(lab)
    assert {i.name for i in items} == {"Aluguel", "Celular"}
    assert await _expenses(lab) == 0  # setting a bill up is not spending
    listing = await lab.say("minhas contas fixas")
    assert "Aluguel" in listing and "faltam 10 de 10" in listing and "Total por mês" in listing


@db
async def test_listing_when_empty(lab: Lab) -> None:
    assert "ainda não tem contas fixas" in await lab.say("contas fixas")


@db
async def test_paying_creates_one_expense_and_moves_the_due_date(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    before = (await _items(lab))[0].next_due_date
    reply = await lab.say("paguei o aluguel")
    assert "Aluguel" in reply and "€1.200,00" in reply
    after = (await _items(lab))[0]
    assert await _expenses(lab) == 1
    assert after.next_due_date == advance(before, "monthly", 1)
    exp = (await lab.rows(select(Expense).where(Expense.member_id == lab.member_id)))[0][0]
    assert (exp.amount, exp.category, exp.merchant, exp.status) == (
        1200,
        "wonen",
        "Aluguel",
        "paid",
    )


@db
async def test_installments_count_up_and_close_after_the_last(lab: Lab) -> None:
    await lab.say("celular em 2x de 50")
    first = await lab.say("paguei o celular")
    assert "1/2" in first
    last = await lab.say("paguei o celular")
    assert "última parcela" in last
    item = (await _items(lab))[0]
    assert item.active is False and item.installments_paid == 2
    assert await _expenses(lab) == 2
    assert "ainda não tem contas fixas" in await lab.say("minhas contas fixas")


@db
async def test_paid_with_an_unknown_name_is_left_to_the_normal_flow(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    await lab.say("paguei o dentista")
    assert await _expenses(lab) == 0
    assert (await _items(lab))[0].installments_paid == 0


@db
async def test_paid_with_a_number_is_a_normal_expense_not_a_bill(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    lab.expense.return_value = {
        "amount": 35.0, "currency": "EUR", "merchant": "Shell", "category": "transport",
        "description": "gasolina", "type": "expense", "days_ago": 0,
    }  # fmt: skip
    await lab.say("paguei 35 de gasolina")
    assert (await _items(lab))[0].installments_paid == 0
    assert await _expenses(lab) == 1


@db
async def test_ambiguous_name_asks_for_the_full_name(lab: Lab) -> None:
    await lab.say("seguro casa 40 mensal")
    await lab.say("seguro carro 60 mensal")
    reply = await lab.say("paguei o seguro")
    assert "Seguro casa" in reply and "Seguro carro" in reply
    assert await _expenses(lab) == 0


@db
async def test_cancel_removes_only_a_matching_bill(lab: Lab) -> None:
    await lab.say("netflix 13,99 mensal")
    assert "removida" in await lab.say("cancela o netflix")
    assert await _items(lab) == []
    await lab.say("netflix 13,99 mensal")
    await lab.say("cancela a viagem")  # no such bill: normal flow, nothing deleted
    assert len(await _items(lab)) == 1


@db
async def test_task_deletion_is_not_hijacked_by_cancel(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    await lab.say("tarefa: pagar aluguel")
    await lab.say("apaga tarefa pagar aluguel")
    assert len(await _items(lab)) == 1


@db
async def test_paying_a_bill_can_trigger_the_budget_alert(lab: Lab) -> None:
    await lab.say("orçamento habitação 1000")
    await lab.say("aluguel 1200 todo dia 1")
    reply = await lab.say("paguei o aluguel")
    assert "estourou" in reply


@db
async def test_limit_of_active_items(lab: Lab) -> None:
    for i in range(30):
        await lab.say(f"conta numero{chr(97 + i % 26)}{chr(97 + i // 26)} {10 + i} mensal")
    assert len(await _items(lab)) == 30
    assert "limite" in await lab.say("extra 10 mensal")
    assert len(await _items(lab)) == 30


@db
async def test_bills_are_exported_with_the_member(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    async with AsyncSessionLocal() as s:
        data = await export_member_data(s, await s.get(Member, lab.member_id))
    assert len(data["tables"]["recurring_item"]) == 1


@db
async def test_all_texts_exist_in_five_languages() -> None:
    from alfred.conversation import _STRINGS
    from alfred.recurring import STRINGS

    for key in STRINGS:
        assert set(_STRINGS[key]) >= {"pt", "nl", "en", "fr", "de"}, key


# ── reminders (cron) ──────────────────────────────────────────────────────────

TEN = now_local().replace(hour=10, minute=0, second=0, microsecond=0)


async def _bill(lab: Lab, days_ahead: int = 2, **kw) -> uuid.UUID:
    item = RecurringItem(
        id=uuid.uuid4(),
        member_id=lab.member_id,
        household_id=lab.household_id,
        name="Aluguel",
        amount=1200,
        category="wonen",
        next_due_date=today_local() + timedelta(days=days_ahead),
        due_day=1,
        **kw,
    )
    await lab.add(item)
    return item.id


async def _inbound(lab: Lab, hours_ago: float) -> None:
    await lab.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id=f"in-{uuid.uuid4()}",
            household_id=lab.household_id,
            author_id=lab.member_id,
            direction="inbound",
            body="oi",
            created_at=TEN
            - timedelta(hours=hours_ago),  # relative to the pinned clock, not the wall clock
        )
    )


async def _pending(lab: Lab, now=TEN):
    async with AsyncSessionLocal() as s:
        return [r for r in await pending_reminders(s, now) if r.wa_phone == lab.phone]


@db
async def test_reminder_three_days_before_and_only_once_per_due_date(lab: Lab) -> None:
    item_id = await _bill(lab, days_ahead=3)
    (r,) = await _pending(lab)
    assert "Aluguel" in r.text and "3 dias" in r.text
    async with AsyncSessionLocal() as s:
        await mark_reminded(s, item_id, r.due)
        await s.commit()
    assert await _pending(lab) == []


@db
async def test_no_reminder_before_the_window_or_outside_waking_hours(lab: Lab) -> None:
    await _bill(lab, days_ahead=4)
    assert await _pending(lab) == []
    await _bill(lab, days_ahead=1)
    assert await _pending(lab, now=TEN.replace(hour=3)) == []
    assert await _pending(lab, now=TEN.replace(hour=21)) == []
    assert len(await _pending(lab)) == 1


@db
async def test_overdue_bill_is_reminded_once(lab: Lab) -> None:
    await _bill(lab, days_ahead=-2)
    (r,) = await _pending(lab)
    assert "venceu" in r.text


@db
async def test_member_without_consent_gets_nothing(lab: Lab) -> None:
    await _bill(lab, days_ahead=1)
    async with AsyncSessionLocal() as s:
        (await s.get(Member, lab.member_id)).consent_state = "rejected"
        await s.commit()
    assert await _pending(lab) == []


@db
async def test_window_flag_follows_the_last_inbound_message(lab: Lab) -> None:
    await _bill(lab, days_ahead=1)
    assert (await _pending(lab))[0].in_window is False
    await _inbound(lab, hours_ago=30)
    assert (await _pending(lab))[0].in_window is False
    await _inbound(lab, hours_ago=2)
    assert (await _pending(lab))[0].in_window is True


def _cron():
    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent / "scripts" / "daily_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@db
async def test_cron_sends_text_in_window_and_marks_it_sent(lab: Lab) -> None:
    await _bill(lab, days_ahead=1)
    await _inbound(lab, hours_ago=1)
    sent: list[tuple[str, str]] = []

    async def fake_text(to, text):
        sent.append((to, text))

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_payment_reminders(TEN)
        await _cron().send_payment_reminders(TEN)  # a second run must not repeat it
    mine = [t for to, t in sent if to == lab.phone]
    assert len(mine) == 1 and "amanhã" in mine[0]


@db
async def test_cron_outside_window_waits_for_the_template_flag(lab: Lab, monkeypatch) -> None:
    await _bill(lab, days_ahead=1)
    calls: list[dict] = []

    async def fake_template(**kw):
        calls.append(kw)

    monkeypatch.setattr(settings, "payment_reminder_template_enabled", False)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_payment_reminders(TEN)
    assert [c for c in calls if c["to"] == lab.phone] == []
    assert len(await _pending(lab)) == 1  # not marked: it will go out once the template exists

    monkeypatch.setattr(settings, "payment_reminder_template_enabled", True)
    with patch("alfred.whatsapp.send_template", fake_template):
        await _cron().send_payment_reminders(TEN)
    mine = [c for c in calls if c["to"] == lab.phone]
    assert len(mine) == 1 and mine[0]["template_name"] == "alfred_payment_reminder"
    assert mine[0]["lang_code"] == "pt_BR"
    assert await _pending(lab) == []


@db
async def test_a_failed_send_does_not_stop_the_others_and_is_retried(lab: Lab) -> None:
    await _bill(lab, days_ahead=1)
    await _inbound(lab, hours_ago=1)

    async def boom(to, text):
        raise RuntimeError("meta down")

    with patch("alfred.whatsapp.send_text", boom):
        _, errors = await _cron().send_payment_reminders(TEN)
    assert errors >= 1
    assert len(await _pending(lab)) == 1  # still pending: tried again on the next run
