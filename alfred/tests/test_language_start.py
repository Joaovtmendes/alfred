"""The language of the first message rules the sign-up invite; when nothing shows it, the phone's
country decides and the page lets the person change it (member.language is the only source)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alfred.conversation import _t, handle_inbound
from alfred.signup import guess_language
from tests.conftest import make_member, make_message, make_session

CTA = "alfred.signup.send_cta_url"


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
async def test_a_clear_first_message_sets_the_language_of_the_invite(first: str, lang: str) -> None:
    member = make_member("pending", language=None)
    with patch(CTA, new_callable=AsyncMock) as cta:
        await handle_inbound(member, make_message(first), make_session())
    assert member.consent_state == "pending_signup" and member.language == lang
    assert cta.await_args.args[1] == _t("signup_invite", lang)


@pytest.mark.parametrize(
    ("phone", "lang"),
    [
        ("5511999990000", "pt"),
        ("31612345678", "nl"),
        ("4915112345678", "de"),
        ("14155550123", "en"),
    ],
)
async def test_an_unclear_first_message_falls_back_to_the_phone_country(
    phone: str, lang: str
) -> None:
    member = make_member("pending", language=None, wa_phone=phone)
    with patch(CTA, new_callable=AsyncMock) as cta:
        await handle_inbound(member, make_message("asdf"), make_session())
    assert member.language == lang == guess_language(phone)
    assert cta.await_args.args[1] == _t("signup_invite", lang)
