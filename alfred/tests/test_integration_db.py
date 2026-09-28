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
