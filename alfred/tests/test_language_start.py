"""The language is asked at the start when the first message does not show it, and the answer
rules both the chat and the panel (member.language is the only source)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alfred.conversation import _t, handle_inbound
from tests.conftest import make_member, make_message, make_session

SEND = "alfred.conversation.send_text"


@pytest.mark.parametrize("first", ["asdf", "👍", "teste123", "qwerty"])
async def test_an_unclear_first_message_asks_which_language(first: str) -> None:
    member = make_member("pending", language=None)
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(first), make_session())
    assert member.consent_state == "pending_language"
    send.assert_awaited_once_with(member.wa_phone, _t("language_ask", "pt"))
    text = _t("language_ask", "pt")
    for name in ("Português", "Nederlands", "English", "Français", "Deutsch"):
        assert name in text  # the same menu in every language: the reader may not know any yet


@pytest.mark.parametrize(
    ("first", "lang"),
    [
        ("oi", "pt"),
        ("hallo, ik wil beginnen", "nl"),
        ("bonjour", "fr"),
        ("guten tag", "de"),
        ("hello", "en"),
    ],
)
async def test_a_clear_first_message_skips_the_question(first: str, lang: str) -> None:
    member = make_member("pending", language=None)
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(first), make_session())
    assert member.consent_state == "pending_response" and member.language == lang
    send.assert_awaited_once_with(member.wa_phone, _t("disclosure", lang))


@pytest.mark.parametrize(
    ("reply", "lang"),
    [
        ("1", "pt"), ("2", "nl"), ("3", "en"), ("4", "fr"), ("5", "de"),
        ("Português", "pt"), ("portugues", "pt"), ("Nederlands", "nl"), ("english", "en"),
        ("Français", "fr"), ("francais", "fr"), ("Deutsch", "de"), ("de", "de"), ("EN", "en"),
    ],
)  # fmt: skip
async def test_the_answer_sets_the_language_and_continues_with_the_disclosure(
    reply: str, lang: str
) -> None:
    member = make_member("pending_language", language="pt")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(reply), make_session())
    assert member.language == lang and member.consent_state == "pending_response"
    send.assert_awaited_once_with(member.wa_phone, _t("disclosure", lang))


@pytest.mark.parametrize("reply", ["talvez", "6", "0", "sim", ""])
async def test_an_unclear_answer_asks_again_and_keeps_the_state(reply: str) -> None:
    member = make_member("pending_language", language="pt")
    with patch(SEND, new_callable=AsyncMock) as send:
        await handle_inbound(member, make_message(reply), make_session())
    assert member.consent_state == "pending_language"
    send.assert_awaited_once_with(member.wa_phone, _t("language_ask", "pt"))
