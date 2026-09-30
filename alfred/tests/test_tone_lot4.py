"""V1-16 — tone lot 4: the same warm voice in NL/EN/FR/DE, activities in the user's language."""

from __future__ import annotations

import re

import pytest

from alfred.conversation import _STRINGS, _t
from alfred.labels import _ACTIVITIES, activity_label

LANGS = ("pt", "nl", "en", "fr", "de")
_FIELD = re.compile(r"\{(\w+)\}")


def _variants(key: str, lang: str) -> tuple[str, ...]:
    v = _STRINGS[key][lang]
    return v if isinstance(v, tuple) else (v,)


VARIANT_KEYS = [k for k, v in _STRINGS.items() if isinstance(v["pt"], tuple)]


def test_there_are_variant_keys() -> None:
    assert len(VARIANT_KEYS) >= 15


@pytest.mark.parametrize("key", VARIANT_KEYS)
def test_every_language_has_variants_with_the_same_placeholders(key: str) -> None:
    expected = set(_FIELD.findall(_variants(key, "pt")[0]))
    for lang in LANGS:
        variants = _variants(key, lang)
        assert len(variants) >= 2, (key, lang)
        assert len(set(variants)) == len(variants), (key, lang)
        for text in variants:
            assert set(_FIELD.findall(text)) == expected, (key, lang, text)


@pytest.mark.parametrize("key", VARIANT_KEYS)
@pytest.mark.parametrize("lang", LANGS)
def test_variants_format_without_error(key: str, lang: str) -> None:
    fields = {f: "x" for f in _FIELD.findall(_variants(key, "pt")[0])}
    for _ in range(10):
        assert _t(key, lang, **fields).strip()


@pytest.mark.parametrize("lang", LANGS)
def test_activity_names_follow_the_language(lang: str) -> None:
    for key, names in _ACTIVITIES.items():
        assert activity_label(key, lang) == names[lang]
    assert activity_label("Running", lang) == _ACTIVITIES["running"][lang]  # case-insensitive
    assert activity_label("tennis", lang) == "tennis"  # unknown types pass through
    assert activity_label(None, lang) == _ACTIVITIES["workout"][lang]


def test_portuguese_never_shows_english_activity_names() -> None:
    for key in ("running", "swimming", "cycling", "walking", "strength"):
        assert activity_label(key, "pt") != key


def test_portuguese_workout_sentence_has_no_gender_clash() -> None:
    # "{activity} anotado" clashed with feminine nouns ("corrida anotado"): variants avoid it
    for text in _variants("workout_saved", "pt"):
        assert "{activity} anotado" not in text
