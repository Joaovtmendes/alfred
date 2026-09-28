"""M3 — consent state machine (conversation.handle_inbound).

pending            → disclosure sent (in the detected language) → pending_response
pending_response   + yes  → accepted (timestamp + version recorded)
pending_response   + no   → rejected
pending_response   + ?    → reminder, state unchanged
accepted           + stop → rejected, no LLM call
rejected           + text → ignored
rejected           + START→ disclosure again → pending_response (must re-consent)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alfred.conversation import _t, handle_inbound
from tests.conftest import make_member, make_message, make_session

SEND = "alfred.conversation.send_text"


async def test_pending_sends_disclosure_in_detected_language() -> None:
    member = make_member("pending", language=None)
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message("hallo, ik wil beginnen"), make_session())
    assert member.consent_state == "pending_response"
    send.assert_awaited_once_with(member.wa_phone, _t("disclosure", member.language))


@pytest.mark.parametrize("word", ["sim", "yes", "ok", "aceito", "ja", "oui"])
async def test_consent_accepted(word: str) -> None:
    member = make_member("pending_response")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "accepted"
    assert member.disclosure_accepted_at is not None
    assert member.disclosure_version == "1.0"
    send.assert_awaited_once_with(member.wa_phone, _t("consent_accepted", "pt"))


@pytest.mark.parametrize("word", ["não", "nao", "no", "nee", "stop"])
async def test_consent_rejected(word: str) -> None:
    member = make_member("pending_response")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "rejected"
    send.assert_awaited_once_with(member.wa_phone, _t("consent_rejected", "pt"))


async def test_consent_unknown_reply_keeps_state() -> None:
    member = make_member("pending_response")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message("talvez"), make_session())
    assert member.consent_state == "pending_response"
    send.assert_awaited_once_with(member.wa_phone, _t("consent_unknown", "pt"))


async def test_rejected_member_is_ignored() -> None:
    member = make_member("rejected")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message("olá de novo"), make_session())
    send.assert_not_awaited()
    assert member.consent_state == "rejected"


@pytest.mark.parametrize("word", ["start", "START", "retomar", "hervatten"])
async def test_rejected_member_can_resume_with_start(word: str) -> None:
    member = make_member("rejected")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "pending_response"
    send.assert_awaited_once_with(member.wa_phone, _t("disclosure", "pt"))


def test_rejection_message_tells_user_how_to_resume() -> None:
    for lang in ("pt", "nl", "en", "fr", "de"):
        assert "START" in _t("consent_rejected", lang)


async def test_stop_in_accepted_rejects_without_llm() -> None:
    member = make_member("accepted")
    with (
        patch(SEND, new_callable=AsyncMock) as send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as llm,
    ):
        await handle_inbound(member, make_message("stop"), make_session())
    assert member.consent_state == "rejected"
    llm.assert_not_awaited()
    send.assert_awaited_once()


async def test_accepted_free_text_goes_to_llm() -> None:
    member = make_member("accepted")
    with (
        patch(SEND, new_callable=AsyncMock) as send,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.extract_habit", new_callable=AsyncMock, return_value=None),
        patch(
            "alfred.conversation.generate_reply", new_callable=AsyncMock, return_value="Olá!"
        ) as llm,
    ):
        await handle_inbound(member, make_message("qual é a capital da frança?"), make_session())
    llm.assert_awaited_once()
    send.assert_awaited_once_with(member.wa_phone, "Olá!")
