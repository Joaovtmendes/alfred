# ruff: noqa: E501
"""V2-36 — service records: a note/photo of a service becomes dates the member can track."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal
from alfred.models import Expense, Message, ServiceRecord
from alfred.privacy import erase_member, export_member_data
from alfred.services import (
    add_months,
    due_reminders,
    mark_sent,
    parse_register,
    record_from_reading,
)
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)
PHOTO = b"\xff\xd8\xffservice"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.conversation.today_local", lambda: TODAY)


async def _recs(lab: Lab) -> list[ServiceRecord]:
    rows = await lab.rows(
        select(ServiceRecord)
        .where(ServiceRecord.member_id == lab.member_id)
        .order_by(ServiceRecord.created_at)
    )
    return [r[0] for r in rows]


def S(**kw):
    base = {
        "kind": "service_invoice",
        "provider": "Loodgieter Jansen",
        "description": "Lekkage keuken",
        "total": 180,
        "currency": "EUR",
        "service_date": "2026-10-10",
        "warranty_months": 12,
    }
    base.update(kw)
    return base


# ── pure rules ────────────────────────────────────────────────────────────────


def test_add_months_clamps_to_month_end() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 10, 10), 12) == date(2027, 10, 10)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_text_register_parses_provider_amount_warranty_date() -> None:
    f = parse_register("encanador, 180 €, garantia 12 meses", TODAY)
    assert f["provider"] == "encanador" and f["amount"] == 180 and f["warranty_months"] == 12
    assert f["service_date"] == TODAY
    f = parse_register("Jansen, vervanging boiler, €1.250,50, 2 jaar garantie, 10/09", TODAY)
    assert f["provider"] == "Jansen" and f["description"] == "vervanging boiler"
    assert f["amount"] == 1250.5 and f["warranty_months"] == 24
    assert f["service_date"] == date(2026, 9, 10)
    f = parse_register("painter", TODAY)
    assert f["provider"] == "painter" and f["amount"] is None and f["warranty_months"] is None


@pytest.mark.parametrize(
    "text", ["", "180", "x, garantia 0 meses", "x, garantia 999 meses", "x, 31/02", "x, 20/10/2026"]
)
def test_text_register_refuses_bad_input(text) -> None:
    assert parse_register(text, TODAY) is None


def test_reading_is_untrusted() -> None:
    f = record_from_reading(S(provider="  *Jansen*\n" + "x" * 200, warranty_months="12"), TODAY)
    assert f is not None and "*" not in f["provider"] and len(f["provider"]) <= 60
    assert f["warranty_months"] == 12
    assert record_from_reading(S(provider=None), TODAY) is None
    assert record_from_reading(S(currency="USD"), TODAY)["amount"] is None
    assert (
        record_from_reading(S(total=-5, warranty_months=9999, service_date="2026-12-01"), TODAY)[
            "warranty_months"
        ]
        is None
    )
    assert record_from_reading(S(service_date="2026-12-01"), TODAY)["service_date"] == TODAY


# ── chat ──────────────────────────────────────────────────────────────────────


@db
async def test_text_register_previews_then_saves_on_confirm(lab: Lab) -> None:
    out = await lab.say("guarda serviço: encanador, 180 €, garantia 12 meses")
    assert "encanador" in out and "14/10/2027" in out
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["svc_ok", "svc_exp", "svc_no"]
    (draft,) = await _recs(lab)
    assert draft.status == "draft"
    await lab.tap(lab.buttons[-1][0][0])
    (rec,) = await _recs(lab)
    assert rec.status == "active" and rec.warranty_until == date(2027, 10, 14)
    assert float(rec.amount) == 180.0
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 0
    )


@db
async def test_confirm_with_expense_books_it_once(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, 180 €, garantia 12 meses")
    btn = lab.buttons[-1][1][0]
    await lab.tap(btn)
    await lab.tap(btn)  # second tap finds nothing
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 1
    )
    assert len(await _recs(lab)) == 1


@db
async def test_cancel_and_new_draft_replaces_old(lab: Lab) -> None:
    await lab.say("guarda serviço: a, 10 €")
    await lab.say("guarda serviço: b, 20 €")
    recs = await _recs(lab)
    assert len(recs) == 1 and recs[0].provider == "b"
    await lab.tap(lab.buttons[-1][-1][0])
    assert await _recs(lab) == []


@db
async def test_expired_draft_is_refused(lab: Lab) -> None:
    await lab.say("guarda serviço: a, 10 €")
    async with AsyncSessionLocal() as s:
        rec = (await s.execute(select(ServiceRecord))).scalars().first()
        rec.created_at = datetime.now(UTC) - timedelta(minutes=30)
        await s.commit()
    await lab.tap(lab.buttons[-1][0][0])
    assert (await _recs(lab)) == []


@db
async def test_photo_flows_into_a_preview(lab: Lab) -> None:
    out = await lab.media("image", PHOTO, S())
    assert "Loodgieter Jansen" in out and "10/10/2026" in out
    (draft,) = await _recs(lab)
    assert draft.status == "draft" and draft.warranty_until == date(2027, 10, 10)


@db
async def test_withdrawal_question_only_when_inside_14_days(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, 180 €, garantia 12 meses")  # today: still in window
    out = await lab.tap(lab.buttons[-1][0][0])
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["svc_wd_yes", "svc_wd_no"]
    await lab.tap(lab.buttons[-1][0][0])
    (rec,) = await _recs(lab)
    assert rec.withdrawal_until == TODAY + timedelta(days=14)
    assert out

    await lab.say("guarda serviço: pintor, 90 €, 01/09")  # long past: no question
    await lab.tap(lab.buttons[-1][0][0])
    assert lab.buttons[-1] == []


@db
async def test_list_detail_delete(lab: Lab) -> None:
    for text in (
        "guarda serviço: encanador, 180 €, garantia 12 meses",
        "guarda serviço: pintor, 90 €",
    ):
        await lab.say(text)
        await lab.tap(lab.buttons[-1][0][0])
    out = await lab.say("minhas garantias")
    assert "encanador" in out and "pintor" in out and "365" in out
    out = await lab.say("garantia encanador")
    assert "14/10/2027" in out and "logo que" in out
    await lab.say("apaga garantia pintor")
    assert [r.provider for r in await _recs(lab)] == ["encanador"]
    assert "encanador" in await lab.say("garantias")


@db
async def test_unmatched_detail_falls_through(lab: Lab) -> None:
    out = await lab.say("garantia de bateria do carro, vale a pena?")
    assert out == "[llm]"


@db
async def test_limit_of_50_active(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        for i in range(50):
            s.add(
                ServiceRecord(
                    member_id=lab.member_id,
                    kind="service",
                    provider=f"p{i}",
                    service_date=TODAY,
                    status="active",
                )
            )
        await s.commit()
    out = await lab.say("guarda serviço: extra, 10 €")
    assert "50" in out
    assert len(await _recs(lab)) == 50


@db
async def test_export_and_erase_cover_service_records(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, 180 €")
    await lab.tap(lab.buttons[-1][0][0])
    async with AsyncSessionLocal() as s:
        from alfred.models import Member

        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
        assert "encanador" in str(data)
        await erase_member(s, member)
        await s.commit()
    assert await lab.scalar(select(func.count()).select_from(ServiceRecord)) == 0


# ── reminders ─────────────────────────────────────────────────────────────────


async def _active(lab: Lab, **kw) -> ServiceRecord:
    async with AsyncSessionLocal() as s:
        rec = ServiceRecord(
            member_id=lab.member_id,
            kind="service",
            provider="encanador",
            service_date=TODAY - timedelta(days=300),
            status="active",
            **kw,
        )
        s.add(rec)
        await s.commit()
        return rec


@db
async def test_warranty_reminders_30_then_7_once_each(lab: Lab) -> None:
    rec = await _active(lab, warranty_until=TODAY + timedelta(days=29))
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.record_id == rec.id and r.which == "w30" and "encanador" in r.text
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY, datetime.now(UTC)) == []
        (r,) = await due_reminders(s, TODAY + timedelta(days=22), datetime.now(UTC))
        assert r.which == "w7"
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY + timedelta(days=25), datetime.now(UTC)) == []


@db
async def test_close_deadline_sends_only_the_urgent_one(lab: Lab) -> None:
    await _active(lab, warranty_until=TODAY + timedelta(days=5))
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.which == "w7"
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY, datetime.now(UTC)) == []


@db
async def test_withdrawal_reminder_3_days_before_and_expired_ignored(lab: Lab) -> None:
    await _active(lab, withdrawal_until=TODAY + timedelta(days=3))
    await _active(lab, warranty_until=TODAY - timedelta(days=1))
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.which == "wd"


@db
async def test_in_window_flag_follows_last_inbound(lab: Lab) -> None:
    await _active(lab, warranty_until=TODAY + timedelta(days=10))
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.in_window is False
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
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.in_window is True
