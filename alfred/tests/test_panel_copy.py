"""User-facing promises about the panel link must match the code (TTL 7 days, sliding renewal)."""

from __future__ import annotations

import re

import pytest

from alfred import panel_i18n
from alfred.conversation import _STRINGS
from alfred.legal import POLICY
from alfred.settings import settings

LANGS = ("pt", "nl", "en", "fr", "de")


def test_policy_does_not_promise_the_old_90_days() -> None:
    assert settings.dashboard_token_ttl_days == 7
    for lang, policy in POLICY.items():
        text = " ".join(p for _, paras in policy["sections"] for p in paras)
        assert "90" not in text, lang
        assert re.search(rf"\b{settings.dashboard_token_ttl_days}\b", text), lang
        assert str(settings.export_token_ttl_minutes) in text, lang  # export link is 15 min


@pytest.mark.parametrize("lang", LANGS)
def test_link_validity_texts_say_up_to_seven_days(lang) -> None:
    """A reused link has between 3.5 and 7 days left, so "valid for 7 days" would be false."""
    shell = panel_i18n.shell(lang)["link_valid"]
    cta = _STRINGS["dashboard_cta"][lang]
    up_to = {
        "pt": "até",
        "nl": "maximaal",
        "en": "up to",
        "fr": "jusqu'à",
        "de": "bis zu",
    }[lang]
    assert up_to in shell and "7" in shell
    assert up_to in cta and "7" in cta


def test_pt_panel_texts_use_straight_quotes() -> None:
    for key, names in panel_i18n.SHELL.items():
        assert "“" not in names["pt"] and "”" not in names["pt"], key
