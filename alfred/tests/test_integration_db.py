"""Integration — full webhook → DB → handler path against a real PostgreSQL.

Skipped unless ALFRED_TEST_DB=1 and DATABASE_URL points at a migrated test DB:

    DATABASE_URL=postgresql+asyncpg://... alembic upgrade head
    ALFRED_TEST_DB=1 DATABASE_URL=postgresql+asyncpg://... pytest tests/test_integration_db.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, func, select

from alfred.db import AsyncSessionLocal, engine
from alfred.models import Expense, Household, Member, Message
from tests.conftest import TEST_APP_SECRET

pytestmark = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.fixture(autouse=True)
async def _fresh_engine():
    yield
    await engine.dispose()  # each test runs in its own event loop


def _payload(phone: str, text: str) -> bytes:
    msg = {
        "id": f"wamid.{uuid.uuid4().hex}",
        "from": phone,
        "timestamp": str(int(time.time())),
        "type": "text",
        "text": {"body": text},
    }
    value = {"messages": [msg], "contacts": [{"wa_id": phone, "profile": {"name": "IT"}}]}
    return json.dumps({"entry": [{"changes": [{"value": value}]}]}).encode()


async def _post(client, body: bytes):
    sig = "sha256=" + hmac.new(TEST_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
    )


async def _cleanup(phone: str) -> None:
    async with AsyncSessionLocal() as s:
        member = (await s.execute(select(Member).where(Member.wa_phone == phone))).scalar()
        if member:
            await s.execute(delete(Expense).where(Expense.member_id == member.id))
            await s.execute(delete(Message).where(Message.household_id == member.household_id))
            hh = member.household_id
            await s.delete(member)
            await s.flush()
            await s.execute(delete(Household).where(Household.id == hh))
        await s.commit()


async def test_first_message_creates_member_and_sends_disclosure(client) -> None:
    phone = "3160" + uuid.uuid4().hex[:7]
    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as send:
        resp = await _post(client, _payload(phone, "olá"))
    assert resp.status_code == 200
    send.assert_awaited_once()
    async with AsyncSessionLocal() as s:
        member = (await s.execute(select(Member).where(Member.wa_phone == phone))).scalar_one()
        assert member.consent_state == "pending_response"
    await _cleanup(phone)


async def test_duplicate_delivery_is_processed_once(client) -> None:
    phone = "3160" + uuid.uuid4().hex[:7]
    body = _payload(phone, "olá")
    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as send:
        await _post(client, body)
        await _post(client, body)  # Meta retry with the same wamid
    assert send.await_count == 1
    await _cleanup(phone)


async def test_handler_failure_rolls_back_partial_writes_but_keeps_message(client) -> None:
    phone = "3160" + uuid.uuid4().hex[:7]
    boom = AsyncMock(side_effect=RuntimeError("whatsapp down"))
    with patch("alfred.conversation.send_text", boom):
        resp = await _post(client, _payload(phone, "olá"))
    assert resp.status_code == 200  # Meta must always get 200
    async with AsyncSessionLocal() as s:
        member = (await s.execute(select(Member).where(Member.wa_phone == phone))).scalar_one()
        # consent change was rolled back with the savepoint → retried next message
        assert member.consent_state == "pending"
        n = (await s.execute(select(func.count()).where(Message.author_id == member.id))).scalar()
        assert n == 1
    await _cleanup(phone)


async def test_llm_history_does_not_repeat_current_message(client) -> None:
    phone = "3160" + uuid.uuid4().hex[:7]
    with patch("alfred.conversation.send_text", new_callable=AsyncMock):
        await _post(client, _payload(phone, "olá"))
        await _post(client, _payload(phone, "sim"))  # accept consent
    llm = AsyncMock(return_value="Paris.")
    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock),
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.extract_habit", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.generate_reply", llm),
    ):
        await _post(client, _payload(phone, "qual é a capital da frança?"))
    llm.assert_awaited_once()
    call = llm.await_args
    history = call.kwargs.get("history", call.args[2] if len(call.args) > 2 else [])
    assert history, "history should contain the earlier turns"
    assert all("capital da frança" not in h["content"] for h in history)
    await _cleanup(phone)


async def test_cron_keeps_going_after_a_failed_send(monkeypatch) -> None:
    """Regression: a failed send used to expire ORM rows and crash the whole run."""
    import importlib.util
    from datetime import UTC, datetime
    from pathlib import Path

    from alfred.models import ScheduledJob

    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent / "scripts" / "daily_cron.py"
    )
    cron = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cron)

    phone = "3160" + uuid.uuid4().hex[:7]
    now_local = datetime.now(UTC).astimezone(cron.ZoneInfo("Europe/Amsterdam"))
    hhmm = now_local.strftime("%H:%M")
    async with AsyncSessionLocal() as s:
        hh = Household(name="cron")
        s.add(hh)
        await s.flush()
        m = Member(household_id=hh.id, wa_phone=phone, consent_state="accepted", language="pt")
        s.add(m)
        await s.flush()
        for jt in ("medication_reminder", "workout_reminder"):
            s.add(ScheduledJob(member_id=m.id, job_type=jt, time_of_day=hhmm, days_mask=127))
        await s.commit()
        member_id = m.id

    calls = []

    async def flaky_send(**kw):
        calls.append(kw["template_name"])
        if len(calls) == 1:
            raise RuntimeError("template not approved")
        return {}

    with patch("alfred.whatsapp.send_template", flaky_send):
        errors = await cron.run_cron()

    mine = [c for c in calls if c in ("alfred_medication_reminder", "alfred_workout_reminder")]
    assert len(mine) == 2 and errors >= 1
    async with AsyncSessionLocal() as s:
        jobs = (
            (await s.execute(select(ScheduledJob).where(ScheduledJob.member_id == member_id)))
            .scalars()
            .all()
        )
        assert sum(j.last_sent_at is not None for j in jobs) == 1  # only the successful one
        for j in jobs:
            await s.delete(j)
        await s.commit()
    await _cleanup(phone)


# ── two-phase webhook (1.7) ───────────────────────────────────────────────────


async def test_webhook_stores_then_acknowledges_then_processes(client) -> None:
    """The 200 must not depend on the handler; the message is durable before processing."""
    from alfred.webhook import Inbound

    phone = "3160" + uuid.uuid4().hex[:7]
    seen: list[Inbound] = []

    async def fake_dispatch(item: Inbound) -> bool:
        async with AsyncSessionLocal() as s:  # committed before the background task runs
            msg = await s.get(Message, item.message_id)
            assert msg is not None and msg.processed is False
        seen.append(item)
        return True

    with patch("alfred.webhook.dispatch_inbound", fake_dispatch):
        resp = await _post(client, _payload(phone, "olá"))
    assert resp.status_code == 200 and len(seen) == 1
    await _cleanup(phone)


async def test_processed_flag_is_set_after_handling(client) -> None:
    phone = "3160" + uuid.uuid4().hex[:7]
    with patch("alfred.conversation.send_text", new_callable=AsyncMock):
        await _post(client, _payload(phone, "olá"))
    async with AsyncSessionLocal() as s:
        member = (await s.execute(select(Member).where(Member.wa_phone == phone))).scalar_one()
        inbound = (
            await s.execute(
                select(Message.processed).where(
                    Message.author_id == member.id, Message.direction == "inbound"
                )
            )
        ).scalar_one()
        assert inbound is True
    await _cleanup(phone)


async def test_failed_handler_leaves_message_unprocessed_for_recovery(client) -> None:
    from datetime import UTC, datetime, timedelta

    from alfred.webhook import recover_unprocessed

    phone = "3160" + uuid.uuid4().hex[:7]
    with patch("alfred.conversation.send_text", AsyncMock(side_effect=RuntimeError("down"))):
        resp = await _post(client, _payload(phone, "olá"))
    assert resp.status_code == 200
    async with AsyncSessionLocal() as s:
        member = (await s.execute(select(Member).where(Member.wa_phone == phone))).scalar_one()
        msg = (await s.execute(select(Message).where(Message.author_id == member.id))).scalar_one()
        assert msg.processed is False
        msg_id = msg.id

    # too fresh → left alone (the original task may still be running)
    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as send:
        assert await recover_unprocessed() == 0
        send.assert_not_awaited()

    # five minutes later WhatsApp is back: the sweep answers it exactly once
    later = datetime.now(UTC) + timedelta(minutes=5)
    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as send:
        assert await recover_unprocessed(now=later) == 1
        assert await recover_unprocessed(now=later) == 0  # processed now
        send.assert_awaited_once()
    async with AsyncSessionLocal() as s:
        assert (await s.get(Message, msg_id)).processed is True

    # far too old → never re-driven
    async with AsyncSessionLocal() as s:
        row = await s.get(Message, msg_id)
        row.processed = False
        await s.commit()
    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as send:
        assert await recover_unprocessed(now=datetime.now(UTC) + timedelta(hours=2)) == 0
        send.assert_not_awaited()
    await _cleanup(phone)
