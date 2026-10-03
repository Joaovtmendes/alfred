# ruff: noqa: E501
from __future__ import annotations

import importlib.util
import os
import pathlib
import string
import sys
from datetime import date

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


# ── the 12 rules (Resumo e Dinheiro) ─────────────────────────────────────────

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def test_there_are_exactly_twelve_rules() -> None:
    assert len(pp.RULES) == 12
    assert all(callable(r) for r in pp.RULES.values())


def _all_phrases(lang: str) -> list[pp.Phrase]:
    """Every phrase the 12 rules can produce, with data above each threshold."""
    sep = date(2026, 9, 1)
    out = [
        pp.rule_balance_vs_projection(1842.3, 1120.0, lang),
        pp.rule_balance_vs_projection(1842.3, None, lang, insufficient=True, committed=298.0),
        pp.rule_budget_over("Restaurantes", 85, 100, lang),
        pp.rule_budget_over("Restaurantes", 148, 120, lang),
        pp.rule_budget_pace("Supermercado", 312, 400, 2, lang),
        pp.rule_category_move(
            [("Restaurantes", 148, 96), ("Lazer", 94, 105)], 6, pp.prev_fragment(sep, lang), lang
        ),
        pp.rule_category_move([("Transporte", 61, 94)], 6, pp.prev_fragment(None, lang), lang),
        pp.rule_upcoming(3, 298, 0, 0, "Energia", lang),
        pp.rule_upcoming(3, 298, 1, 118, "Energia", lang),
        pp.rule_owed("Marta", 34.5, 9, lang),
        pp.rule_blue_days(9, 14, 9, lang),
        pp.rule_largest_entry(
            23, "Aluguel", 1150, pp.period_fragment(date(2026, 10, 1), lang), lang
        ),
        pp.rule_top_share("Aluguel", 1150, 1997.7, 12, lang),
        pp.rule_mom_driver("Restaurantes", 52, 60, lang),
        pp.rule_mom_driver("Restaurantes", 52, 55, lang),
        pp.rule_fixed_variable(1238, 760, lang),
        pp.rule_busiest_day(date(2026, 10, 18), 230, 8, 14, 23, lang),
    ]
    assert all(out), out
    out += [pp.empty_hint(k, lang) for k in pp.EMPTY]
    return out


@pytest.mark.parametrize("lang", LANGS)
def test_every_rule_renders_in_every_language_with_a_quoted_chat_command(lang) -> None:
    for p in _all_phrases(lang):
        assert "{" not in p.text and "}" not in p.text, (p.key, lang, p.text)
        assert p.severity in ("info", "attention")
        assert p.chat and p.chat.startswith('"') and p.chat.endswith('"'), (p.key, lang)
        assert p.chat.count('"') == 2 and "{" not in p.chat, (p.key, lang, p.chat)
        low = p.text.lower()
        for stem in pp.FORBIDDEN_STEMS[lang]:
            assert stem not in low, (p.key, lang, stem)


def test_every_catalogue_key_is_used_by_a_rule_and_every_chat_key_too() -> None:
    produced = {p.key for lang in ("pt",) for p in _all_phrases(lang)}
    assert produced == set(pp.PHRASES), set(pp.PHRASES) ^ produced
    used_chat = {c for c in pp.CHAT if any(p.chat == pp.CHAT[c]["pt"] for p in _all_phrases("pt"))}
    # the keys with a value inside are compared by their template, the rest by value
    assert {"budget", "mom", "blue_days", "entries", "top", "fixed", "log_expense", "set_budget",
            "add_recurring", "add_owed", "balance_projection"} <= used_chat  # fmt: skip


def test_phrases_never_contain_a_cause_or_advice_word_in_any_catalogue() -> None:
    for catalogue in (pp.PHRASES, pp.LABELS):
        for key, by_lang in catalogue.items():
            for lang, text in by_lang.items():
                low = text.lower()
                assert not [s for s in pp.FORBIDDEN_STEMS[lang] if s in low], (key, lang)


@pytest.mark.parametrize(
    ("call", "expected_none"),
    [
        # rule 2: below 80 %, no limit, zero spent
        (lambda: pp.rule_budget_over("X", 0, 100, "pt"), True),
        (lambda: pp.rule_budget_over("X", -5, 100, "pt"), True),
        (lambda: pp.rule_budget_over("X", 80, 100, "pt"), False),
        # rule 3: not within a week, already at 80 %, no estimate
        (lambda: pp.rule_budget_pace("X", 50, 100, 8, "pt"), True),
        (lambda: pp.rule_budget_pace("X", 80, 100, 2, "pt"), True),
        (lambda: pp.rule_budget_pace("X", 50, 100, None, "pt"), True),
        (lambda: pp.rule_budget_pace("X", 50, 100, 7, "pt"), False),
        # rule 4: previous period too thin, move too small (EUR or %), nothing moved
        (lambda: pp.rule_category_move([("A", 100, 50)], 2, "em x", "pt"), True),
        (lambda: pp.rule_category_move([("A", 108, 100)], 5, "em x", "pt"), True),
        (lambda: pp.rule_category_move([("A", 1005, 1000)], 5, "em x", "pt"), True),
        (lambda: pp.rule_category_move([], 5, "em x", "pt"), True),
        (lambda: pp.rule_category_move([("A", 30, 0)], 5, "em x", "pt"), False),  # new category
        # rule 5: nothing upcoming and nothing overdue
        (lambda: pp.rule_upcoming(0, 0, 0, 0, None, "pt"), True),
        (lambda: pp.rule_upcoming(0, 0, 1, 50, None, "pt"), False),
        # rule 6: a week, no amount, no person
        (lambda: pp.rule_owed("Marta", 34.5, 6, "pt"), True),
        (lambda: pp.rule_owed("Marta", 34.5, 7, "pt"), False),
        (lambda: pp.rule_owed("Marta", 0, 30, "pt"), True),
        (lambda: pp.rule_owed("", 5, 30, "pt"), True),
        # rule 7: a week of data
        (lambda: pp.rule_blue_days(5, 6, 5, "pt"), True),
        (lambda: pp.rule_blue_days(0, 7, 0, "pt"), False),
        # rule 8: five entries, a name
        (lambda: pp.rule_largest_entry(4, "A", 10, "em x", "pt"), True),
        (lambda: pp.rule_largest_entry(5, None, 10, "em x", "pt"), True),
        (lambda: pp.rule_largest_entry(5, "A", 0, "em x", "pt"), True),
        # rule 9: 30 %, three entries, a total
        (lambda: pp.rule_top_share("A", 29, 100, 5, "pt"), True),
        (lambda: pp.rule_top_share("A", 30, 100, 5, "pt"), False),
        (lambda: pp.rule_top_share("A", 90, 100, 2, "pt"), True),
        (lambda: pp.rule_top_share("A", 5, 0, 5, "pt"), True),
        # rule 10: half of a rise of at least 10, rises only
        (lambda: pp.rule_mom_driver("A", 20, 50, "pt"), True),
        (lambda: pp.rule_mom_driver("A", 25, 50, "pt"), False),
        (lambda: pp.rule_mom_driver("A", 5, 8, "pt"), True),
        (lambda: pp.rule_mom_driver("A", 40, -20, "pt"), True),
        (lambda: pp.rule_mom_driver(None, 40, 50, "pt"), True),
        # rule 11: both parts must exist
        (lambda: pp.rule_fixed_variable(0, 100, "pt"), True),
        (lambda: pp.rule_fixed_variable(100, 0, "pt"), True),
        (lambda: pp.rule_fixed_variable(100, 1, "pt"), False),
        # rule 12: a week and five entries
        (lambda: pp.rule_busiest_day(date(2026, 10, 1), 10, 0, 6, 9, "pt"), True),
        (lambda: pp.rule_busiest_day(date(2026, 10, 1), 10, 0, 7, 4, "pt"), True),
        (lambda: pp.rule_busiest_day(None, 10, 0, 7, 9, "pt"), True),
        (lambda: pp.rule_busiest_day(date(2026, 10, 1), 10, 0, 7, 5, "pt"), False),
    ],
)
def test_rules_stay_silent_below_their_data_threshold(call, expected_none) -> None:
    assert (call() is None) is expected_none


def test_rule_wording_and_severity() -> None:
    mom = pp.rule_category_move([("Restaurantes", 148, 96)], 6, "em setembro", "pt")
    assert (
        mom and mom.text == "Restaurantes é o que mais cresceu: € 52,00 a mais do que em setembro."
    )
    fall = pp.rule_category_move([("Transporte", 61, 94)], 6, "no período anterior", "pt")
    assert fall and fall.key == "categories_fall" and "€ 33,00" in fall.text
    assert pp.rule_mom_driver("R", 52, 55, "pt").text.endswith("quase toda a diferença.")
    assert pp.rule_mom_driver("R", 30, 55, "pt").text.endswith("a maior parte da diferença.")
    ok = pp.rule_upcoming(3, 298, 0, 0, "Energia", "pt")
    assert ok and ok.severity == "info" and ok.chat == '"paguei Energia"'
    assert "3 contas" in ok.text and "€ 298,00" in ok.text and "Nada está atrasado" in ok.text
    late = pp.rule_upcoming(3, 298, 1, 118, "Energia", "pt")
    assert late and late.severity == "attention" and "1 conta em atraso" in late.text
    owed = pp.rule_owed("Marta", 34.5, 9, "pt")
    assert owed and owed.chat == '"Marta pagou 34,50"' and owed.severity == "info"
    assert "9 dias" in owed.text
    assert pp.rule_owed("Marta", 34.5, 30, "pt").severity == "attention"
    assert pp.rule_blue_days(1, 7, 1, "pt").text.endswith("foi de 1 dia.")
    assert "8" in pp.rule_busiest_day(date(2026, 10, 18), 230, 8, 14, 23, "pt").text
    assert "18/10" in pp.rule_busiest_day(date(2026, 10, 18), 230, 8, 14, 23, "pt").text


def test_a_name_with_quotes_cannot_break_out_of_the_suggested_command() -> None:
    p = pp.rule_upcoming(1, 10, 0, 0, 'Luz "casa"\nnova', "pt")
    assert p and p.chat == '"paguei Luz casa nova"' and p.chat.count('"') == 2
    q = pp.rule_owed('Ma"rta', 5, 9, "en")
    assert q and q.chat == '"Marta paid me 5,00"'


def test_edge_numbers_format_without_surprises() -> None:
    assert pp.fmt_eur(-0.004, "pt") == "€ 0,00"
    assert pp.fmt_eur(-120.5, "en") == "− € 120,50"
    assert pp.fmt_eur(pp.Decimal("1842.30"), "de") == "€ 1.842,30"
    neg = pp.rule_balance_vs_projection(-10.0, -250.0, "pt")
    assert neg and "− € 250,00" in neg.text
    zero = pp.rule_balance_vs_projection(0.0, 0.0, "nl")
    assert zero and "€ 0,00" in zero.text
    thin = pp.rule_balance_vs_projection(0.0, None, "pt", insufficient=True, committed=0.0)
    assert thin and thin.key == "projection_insufficient" and "€ 0,00" in thin.text
    assert pp.rule_balance_vs_projection(0.0, None, "pt") is None


def test_singular_plural_and_due_labels() -> None:
    assert pp.unit("day", 1, "pt") == "1 dia" and pp.unit("day", 2, "pt") == "2 dias"
    assert pp.unit("bill", 1, "en") == "1 bill" and pp.unit("bill", 0, "en") == "0 bills"
    assert pp.due_label(4, "pt") == "vence em 4 dias"
    assert pp.due_label(1, "pt") == "vence amanhã" and pp.due_label(0, "pt") == "vence hoje"
    assert pp.due_label(-1, "pt") == "atrasada há 1 dia"
    assert pp.due_label(-3, "en") == "overdue by 3 days"
    assert pp.due_label(6, "de") == "fällig in 6 Tage"  # noqa: RUF001 (the label keeps the unit as is)
    assert pp.month_word(date(2026, 9, 1), "pt") == "setembro"
    assert pp.month_word(date(2026, 9, 1), "de") == "September"
    assert pp.period_fragment(date(2026, 10, 1), "pt") == "em outubro"
    assert pp.period_fragment(None, "pt") == "no período"
    assert pp.prev_fragment(None, "fr") == "dans la période précédente"


# The chat commands the panel suggests must be understood by the router (no LLM). The only
# ones that go to the LLM path are balance_projection and log_expense.
_RUN_ORDER = ("add_recurring", "add_owed", "set_budget")


@db
@pytest.mark.parametrize("lang", LANGS)
async def test_suggested_chat_commands_are_understood_by_the_router(lab, lang) -> None:
    names = {"pt": "Aluguel", "nl": "Huur", "en": "Rent", "fr": "Loyer", "de": "Miete"}
    sample = {"name": names[lang], "person": "Marta", "amount": "34,50"}
    for key in _RUN_ORDER:  # create what the later commands refer to
        reply = await lab.say(pp.CHAT[key][lang].strip('"'))
        assert reply != "[llm]", (key, lang, reply)
    for key in ("budget", "mom", "blue_days", "entries", "top", "fixed", "upcoming", "owed"):
        command = pp.CHAT[key][lang].strip('"').format(**sample)
        reply = await lab.say(command)
        assert reply != "[llm]", (key, lang, command, reply)


def test_message_audit_covers_labels_and_stays_clean() -> None:
    spec = importlib.util.spec_from_file_location(
        "message_audit2", pathlib.Path(__file__).parent.parent / "scripts" / "message_audit.py"
    )
    audit = importlib.util.module_from_spec(spec)
    sys.modules["message_audit2"] = audit
    spec.loader.exec_module(audit)
    findings, _ = audit.run()
    assert not [str(f) for f in findings if f.level == "error"]
    assert not [str(f) for f in findings if "panel." in f.where], [
        str(f) for f in findings if "panel." in f.where
    ]
    assert audit.panel_catalogue_size() == len(pp.PHRASES) + len(pp.CHAT) + len(pp.LABELS)
