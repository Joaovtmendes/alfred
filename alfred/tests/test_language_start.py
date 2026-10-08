"""The sign-up invite and page start in English whatever the first message says; the language the
person picks on the page is what the chat uses from then on (member.language is the only source)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from alfred.conversation import _t, handle_inbound
from tests.conftest import make_member, make_message, make_session

CTA = "alfred.signup.send_cta_url"


@pytest.mark.parametrize(
    "first", ["oi", "hallo, ik wil beginnen", "bonjour", "guten tag", "hello", "asdf", "👍"]
)
async def test_the_first_invite_is_in_english_whatever_the_first_message(first: str) -> None:
    member = make_member("pending", language=None, wa_phone="5511999990000")
    with patch(CTA, new_callable=AsyncMock) as cta:
        await handle_inbound(member, make_message(first), make_session())
    assert member.consent_state == "pending_signup" and member.language == "en"
    assert cta.await_args.args[1] == _t("signup_invite", "en")
    assert cta.await_args.args[2] == _t("signup_button", "en")
