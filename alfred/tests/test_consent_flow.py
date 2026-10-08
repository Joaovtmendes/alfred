"""M3 — consent state machine (conversation.handle_inbound).

pending            → sign-up link (button) in the detected language → pending_signup
pending_signup     + anything → the link again (a chat "sim" does not open the account)
pending_signup     + stop → rejected
(the account becomes ``accepted`` only through the sign-up page: see test_signup_db.py)
accepted           + stop → rejected, no LLM call
rejected           + text → ignored
rejected           + START→ sign-up link again → pending_signup (must re-consent)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alfred.conversation import _t, handle_inbound
from tests.conftest import make_member, make_message, make_session

SEND = "alfred.conversation.send_text"


CTA = "alfred.signup.send_cta_url"


async def test_first_message_sends_the_signup_link_in_the_detected_language() -> None:
    member = make_member("pending", language=None)
    with patch(CTA, new_callable=AsyncMock) as cta, patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message("hallo, ik wil beginnen"), make_session())
    assert member.consent_state == "pending_signup" and member.language == "nl"
    send.assert_not_awaited()
    cta.assert_awaited_once()
    phone, body, button, url = cta.await_args.args
    assert phone == member.wa_phone and body == _t("signup_invite", "nl")
    assert button == _t("signup_button", "nl") and f"/cadastro/{member.signup_token}" in url
    assert member.signup_token_expires_at is not None


@pytest.mark.parametrize("word", ["sim", "yes", "ok", "aceito", "ja", "oui"])
async def test_a_chat_yes_no_longer_opens_the_account(word: str) -> None:
    member = make_member("pending_signup")
    with patch(CTA, new_callable=AsyncMock) as cta, patch(SEND, new_callable=AsyncMock):
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "pending_signup"
    assert member.disclosure_accepted_at is None
    cta.assert_awaited_once()
    assert cta.await_args.args[1] == _t("signup_again", "pt")


async def test_a_fresh_link_is_sent_again_not_replaced() -> None:
    member = make_member("pending", language=None)
    with patch(CTA, new_callable=AsyncMock), patch(SEND, new_callable=AsyncMock):
        await handle_inbound(member, make_message("oi"), make_session())
        first = member.signup_token
        await handle_inbound(member, make_message("oi de novo"), make_session())
    assert member.signup_token == first


@pytest.mark.parametrize("state", ["pending_language", "pending_response"])
async def test_someone_caught_in_the_old_flow_gets_the_link(state: str) -> None:
    member = make_member(state)
    with patch(CTA, new_callable=AsyncMock) as cta, patch(SEND, new_callable=AsyncMock):
        await handle_inbound(member, make_message("sim"), make_session())
    assert member.consent_state == "pending_signup"
    cta.assert_awaited_once()


@pytest.mark.parametrize("word", ["stop", "não", "nao", "nee"])
async def test_stop_while_waiting_for_the_signup_rejects(word: str) -> None:
    member = make_member("pending_signup")
    with patch(CTA, new_callable=AsyncMock) as cta, patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "rejected"
    cta.assert_not_awaited()
    send.assert_awaited_once_with(member.wa_phone, _t("consent_rejected", "pt"))


async def test_rejected_member_is_ignored() -> None:
    member = make_member("rejected")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message("olá de novo"), make_session())
    send.assert_not_awaited()
    assert member.consent_state == "rejected"


@pytest.mark.parametrize("word", ["start", "START", "retomar", "hervatten"])
async def test_rejected_member_can_resume_with_start(word: str) -> None:
    member = make_member("rejected")
    with patch(CTA, new_callable=AsyncMock) as cta, patch(SEND, new_callable=AsyncMock):
        await handle_inbound(member, make_message(word), make_session())
    assert member.consent_state == "pending_signup"
    cta.assert_awaited_once()


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
