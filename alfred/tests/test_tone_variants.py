"""Variant strings must be interchangeable: same placeholders, no leftovers of the old register."""

import random
import string

from alfred.conversation import _STRINGS, _t


def _fields(tmpl: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(tmpl) if f}


def test_variants_share_placeholders() -> None:
    for key, langs in _STRINGS.items():
        for lang, val in langs.items():
            if isinstance(val, tuple):
                assert len(val) >= 2, (key, lang)
                assert len({frozenset(_fields(v)) for v in val}) == 1, (key, lang)


def test_pt_uses_voce_not_tu() -> None:
    banned = ("teus", "tuas", "tens ", "podes", "quiseres", "envia ", "diz-me", "registos")
    for key, val in _STRINGS.items():
        for text in val["pt"] if isinstance(val["pt"], tuple) else (val["pt"],):
            low = text.lower()
            assert not any(b in low for b in banned), (key, text)


def test_variant_choice_is_random_but_valid() -> None:
    random.seed(1)
    seen = {_t("expense_recorded", "pt", amount="€5,00", name="Jumbo") for _ in range(40)}
    assert len(seen) >= 2
    assert all("€5,00" in s and "Jumbo" in s for s in seen)
