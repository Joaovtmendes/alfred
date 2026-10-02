from __future__ import annotations

import importlib.util
import pathlib
import string
import sys

import pytest

from alfred import panel_phrases as pp

LANGS = ("pt", "nl", "en", "fr", "de")


def _placeholders(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_key_exists_in_every_language_with_same_placeholders() -> None:
    for key, by_lang in [*pp.PHRASES.items(), *pp.CHAT.items()]:
        assert set(by_lang) == set(LANGS), key
        assert len({frozenset(_placeholders(t)) for t in by_lang.values()}) == 1, key


def test_no_financial_advice_verbs() -> None:
    for key, by_lang in pp.PHRASES.items():
        for lang, text in by_lang.items():
            low = text.lower()
            for stem in pp.FORBIDDEN_STEMS[lang]:
                assert stem not in low, (key, lang, stem)


def test_forbidden_stems_cover_every_language() -> None:
    assert set(pp.FORBIDDEN_STEMS) == set(LANGS)
    assert all(pp.FORBIDDEN_STEMS[lang] for lang in LANGS)


def test_chat_suggestions_are_quoted_commands_with_straight_quotes() -> None:
    for key, by_lang in pp.CHAT.items():
        for lang, text in by_lang.items():
            assert text.startswith('"') and text.endswith('"'), (key, lang)
            assert text.count('"') == 2, (key, lang)
            assert "“" not in text and "”" not in text, (key, lang)


@pytest.mark.parametrize("lang", LANGS)
def test_european_money_in_every_language(lang) -> None:
    assert pp.fmt_eur(1234.5, lang) == "€ 1.234,50"
    assert pp.fmt_eur(0, lang) == "€ 0,00"
    assert pp.fmt_eur(-5, lang) == "− € 5,00"
    assert pp.fmt_eur(1234567.891, lang) == "€ 1.234.567,89"


def test_budget_rule_thresholds() -> None:
    assert pp.rule_budget_over("Restaurantes", 50, 100, "pt") is None
    assert pp.rule_budget_over("Restaurantes", 79, 100, "pt") is None
    assert pp.rule_budget_over("Restaurantes", 50, 0, "pt") is None  # no limit, no phrase
    warn = pp.rule_budget_over("Restaurantes", 85, 100, "pt")
    assert warn and warn.severity == "info" and warn.key == "budget_near"
    exact = pp.rule_budget_over("Restaurantes", 100, 100, "pt")
    assert exact and exact.severity == "attention" and exact.key == "budget_over"
    over = pp.rule_budget_over("Restaurantes", 123, 100, "pt")
    assert over and over.severity == "attention"
    assert "€ 123,00" in over.text and "123%" in over.text
    assert over.chat and over.chat.startswith('"')


@pytest.mark.parametrize("lang", LANGS)
def test_rules_render_in_every_language(lang) -> None:
    p = pp.rule_budget_over("X", 120, 100, lang)
    assert p and "{" not in p.text and p.chat == pp.CHAT["budget"][lang]
    q = pp.rule_balance_vs_projection(1842.30, 1120.0, lang)
    assert q and "€ 1.120,00" in q.text and "{" not in q.text


def test_unknown_language_falls_back_to_pt() -> None:
    p = pp.rule_balance_vs_projection(10.0, 5.0, "es")
    assert p and p.text == pp.PHRASES["balance_projection"]["pt"].format(projected="€ 5,00")


def test_projection_rule_needs_data() -> None:
    assert pp.rule_balance_vs_projection(1842.30, None, "pt") is None
    p = pp.rule_balance_vs_projection(1842.30, 1120.0, "pt")
    assert p and "€ 1.120,00" in p.text and "estimativa" in p.text.lower()
    assert p.chat == pp.CHAT["balance_projection"]["pt"] and p.severity == "info"


def test_message_audit_reads_the_panel_catalogue() -> None:
    spec = importlib.util.spec_from_file_location(
        "message_audit", pathlib.Path(__file__).parent.parent / "scripts" / "message_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    sys.modules["message_audit"] = audit
    spec.loader.exec_module(audit)
    findings, _ = audit.run()
    assert not [str(f) for f in findings if f.level == "error"]
    assert audit.panel_catalogue_size() >= 5
