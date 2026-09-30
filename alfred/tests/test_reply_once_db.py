"""V1-23 — a handler that fails after replying must not make the user get the reply twice."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from alfred import delivery, webhook, whatsapp
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member, Message

LOG = SimpleNamespace(
    error=lambda *a, **k: None, warning=lambda *a, **k: None, info=lambda *a, **k: None
)


async def _stored(s, lab, tag: str) -> Message:
    m = Message(
        wa_message_id=f"wamid.once.{tag}.{lab.member_id}",
        household_id=lab.household_id,
        author_id=lab.member_id,
        direction="inbound",
        body="x",
    )
    s.add(m)
    await s.flush()
    return m


async def _flags(lab, tag: str) -> tuple[bool, bool]:
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(
                select(Message).where(Message.wa_message_id == f"wamid.once.{tag}.{lab.member_id}")
            )
        ).scalar_one()
        return row.processed, row.reply_sent


async def test_failure_after_reply_flags_and_retry_is_silent(lab, monkeypatch) -> None:
    await engine.dispose()
    seen: list[bool] = []

    async def first_run(member, stored, session) -> None:
        seen.append(delivery.suppressed())
        delivery.note_sent()  # the reply went out...
        raise RuntimeError("boom")  # ...then the handler failed before the commit

    async def second_run(member, stored, session) -> None:
        seen.append(delivery.suppressed())

    monkeypatch.setattr("alfred.conversation.handle_inbound", first_run)
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        stored = await _stored(s, lab, "a")
        assert await webhook._handle_one(member, stored, s, LOG) is False
    assert await _flags(lab, "a") == (False, True)  # still to be redone, but flagged

    monkeypatch.setattr("alfred.conversation.handle_inbound", second_run)
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        stored = (
            await s.execute(select(Message).where(Message.wa_message_id.like("wamid.once.a.%")))
        ).scalar_one()
        assert await webhook._handle_one(member, stored, s, LOG) is True
    assert seen == [False, True]  # the retry ran with sending suppressed
    assert await _flags(lab, "a") == (True, True)
    await engine.dispose()


async def test_failure_before_any_reply_retries_normally(lab, monkeypatch) -> None:
    await engine.dispose()
    seen: list[bool] = []

    async def fails_early(member, stored, session) -> None:
        raise RuntimeError("boom")

    async def works(member, stored, session) -> None:
        seen.append(delivery.suppressed())

    monkeypatch.setattr("alfred.conversation.handle_inbound", fails_early)
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        stored = await _stored(s, lab, "b")
        assert await webhook._handle_one(member, stored, s, LOG) is False
    assert await _flags(lab, "b") == (False, False)  # nothing was sent: the retry may reply

    monkeypatch.setattr("alfred.conversation.handle_inbound", works)
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        stored = (
            await s.execute(select(Message).where(Message.wa_message_id.like("wamid.once.b.%")))
        ).scalar_one()
        assert await webhook._handle_one(member, stored, s, LOG) is True
    assert seen == [False]
    await engine.dispose()


class _FakeClient:
    calls = 0

    def __init__(self, *a, **k) -> None: ...

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a) -> None: ...

    async def post(self, *a, **k):
        type(self).calls += 1
        return httpx.Response(
            200, json={"messages": [{"id": "wamid.x"}]}, request=httpx.Request("POST", "http://x")
        )


async def test_send_functions_respect_suppression_and_count_real_sends(monkeypatch) -> None:
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _FakeClient)
    _FakeClient.calls = 0
    state = delivery.begin(suppress=True)
    assert await whatsapp.send_text("3161", "oi") == {"suppressed": True}
    assert await whatsapp.send_buttons("3161", "oi", [("a", "A")]) == {"suppressed": True}
    assert await whatsapp.send_template("3161", "t", "pt_BR", []) == {"suppressed": True}
    assert _FakeClient.calls == 0 and state.sent == 0

    state = delivery.begin(suppress=False)
    await whatsapp.send_text("3161", "oi")
    await whatsapp.send_buttons("3161", "oi", [("a", "A")])
    assert _FakeClient.calls == 2 and state.sent == 2
    delivery.end()


async def test_outside_a_capture_sending_is_unaffected(monkeypatch) -> None:
    monkeypatch.setattr(whatsapp.httpx, "AsyncClient", _FakeClient)
    delivery.end()
    _FakeClient.calls = 0
    assert (await whatsapp.send_text("3161", "oi"))["messages"][0]["id"] == "wamid.x"
    assert _FakeClient.calls == 1
    assert delivery.suppressed() is False


@pytest.fixture(autouse=True)
def _clean_capture():
    yield
    delivery.end()
