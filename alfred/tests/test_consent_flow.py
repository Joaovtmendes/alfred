"""M3 — Consent flow tests.

Tests every state transition in conversation.handle_inbound:
  pending          → sends disclosure, advances to pending_response
  pending_response + sim   → accepted, sends confirmation
  pending_response + nao   → rejected, sends rejection message
  pending_response + ?     → stays pending_response, sends reminder
  rejected         → silently ignored
  accepted         → routed to LLM, reply sent
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alfred.conversation import (
    CONSENT_ACCEPTED_NL,
    CONSENT_REJECTED_NL,
    CONSENT_UNKNOWN_NL,
    DISCLOSURE_NL,
    handle_inbound,
)
from alfred.models import Member, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_member(consent_state: str = "pending") -> Member:
    m = Member.__new__(Member)
    m.id = uuid.uuid4()
    m.household_id = uuid.uuid4()
    m.wa_phone = "31600000001"
    m.consent_state = consent_state
    m.display_name = "Test"
    m.disclosure_accepted_at = None
    m.disclosure_version = None
    return m


def _make_message(body: str = "hallo") -> Message:
    msg = Message.__new__(Message)
    msg.id = uuid.uuid4()
    msg.body = body
    msg.direction = "inbound"
    msg.wa_message_id = "wamid.test"
    msg.wa_timestamp = datetime.now(timezone.utc)
    return msg


def _mock_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_sends_disclosure() -> None:
    """First contact: disclosure is sent and state advances."""
    member = _make_member("pending")
    message = _make_message("qualquer coisa")
    session = _mock_session()

    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send:
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once_with(member.wa_phone, DISCLOSURE_NL)
    assert member.consent_state == "pending_response"
    session.add.assert_called_once_with(member)


@pytest.mark.parametrize("word", ["sim", "yes", "s", "y", "ok", "aceito", "aceitar", "ja"])
@pytest.mark.asyncio
async def test_consent_accepted(word: str) -> None:
    """User replies with acceptance word → state = accepted."""
    member = _make_member("pending_response")
    message = _make_message(word)
    session = _mock_session()

    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send:
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once_with(member.wa_phone, CONSENT_ACCEPTED_NL)
    assert member.consent_state == "accepted"
    assert member.disclosure_accepted_at is not None
    assert member.disclosure_version == "1.0"


@pytest.mark.parametrize("word", ["não", "nao", "no", "n", "stop", "nee"])
@pytest.mark.asyncio
async def test_consent_rejected(word: str) -> None:
    """User replies with rejection word → state = rejected."""
    member = _make_member("pending_response")
    message = _make_message(word)
    session = _mock_session()

    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send:
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once_with(member.wa_phone, CONSENT_REJECTED_NL)
    assert member.consent_state == "rejected"


@pytest.mark.asyncio
async def test_consent_unknown_reply() -> None:
    """Ambiguous reply during pending_response → reminder sent, state unchanged."""
    member = _make_member("pending_response")
    message = _make_message("talvez")
    session = _mock_session()

    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send:
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once_with(member.wa_phone, CONSENT_UNKNOWN_NL)
    assert member.consent_state == "pending_response"


@pytest.mark.asyncio
async def test_rejected_member_ignored() -> None:
    """Rejected member sends message → no reply sent."""
    member = _make_member("rejected")
    message = _make_message("olá de novo")
    session = _mock_session()

    with patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send:
        await handle_inbound(member, message, session)

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_accepted_member_gets_llm_reply() -> None:
    """Accepted member → generate_reply called and result sent."""
    member = _make_member("accepted")
    message = _make_message("qual é o tempo hoje?")
    session = _mock_session()

    fake_reply = "Hoje está sol em Amesterdão! ☀️"

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.llm.generate_reply", new_callable=AsyncMock, return_value=fake_reply),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once_with(member.wa_phone, fake_reply)


@pytest.mark.asyncio
async def test_accepted_member_empty_body() -> None:
    """Accepted member sends empty message → LLM still called (body='')."""
    member = _make_member("accepted")
    message = _make_message("")
    session = _mock_session()

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.llm.generate_reply", new_callable=AsyncMock, return_value="…"),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
