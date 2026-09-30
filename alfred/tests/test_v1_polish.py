"""V1-18 — loose texts: possessive command forms, singular/plural wording in 5 languages."""

from __future__ import annotations

import re

import pytest

from alfred.conversation import _STRINGS, _SUMMARY_WORDS, _is_bare_command, _t

LANGS = ["pt", "nl", "en", "fr", "de"]


@pytest.mark.parametrize(
    "text",
    [
        "my summary",
        "mijn samenvatting",
        "mon résumé",
        "meine übersicht",
        "meu resumo",
        "o meu resumo",
        "resumo",
    ],
)
def test_possessive_summary_is_a_bare_command(text: str) -> None:
    assert _is_bare_command(text, _SUMMARY_WORDS)


@pytest.mark.parametrize("text", ["my summary of food", "gastos em restaurante", "my"])
def test_possessive_does_not_swallow_longer_queries(text: str) -> None:
    assert not _is_bare_command(text, _SUMMARY_WORDS)


@pytest.mark.parametrize("lang", LANGS)
def test_no_optional_plural_markers_left(lang: str) -> None:
    for key, variants in _STRINGS.items():
        tmpl = variants.get(lang)
        for t in tmpl if isinstance(tmpl, tuple) else (tmpl,):
            assert not re.search(r"\w\((?:s|en|n|e|ões)\)", t or ""), (key, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_singular_keys_say_one_not_many(lang: str) -> None:
    streak = _t("habit_streak_one", lang, activity="yoga")
    assert "1" in streak and "yoga" in streak
    assert not re.search(r"\b1 (days|dagen|jours|Tage)\b", streak.replace("*", ""))
    one = _t("transactions_count_one", lang)
    assert "1" in one and not one.rstrip("_").endswith("s")


@pytest.mark.parametrize("lang", LANGS)
def test_plural_streak_uses_plural_word(lang: str) -> None:
    text = _t("habit_streak", lang, activity="yoga", n=5)
    assert "5" in text
