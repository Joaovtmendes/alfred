"""The eval script's own checker (no LLM involved)."""

from __future__ import annotations

import importlib.util
import pathlib

_spec = importlib.util.spec_from_file_location(
    "eval_llm", pathlib.Path(__file__).parent.parent / "scripts" / "eval_llm.py"
)
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)


def test_check_accepts_and_rejects() -> None:
    assert (
        ev.check({"amount": 10.0, "type": "expense"}, {"amount": 10.0, "type": "expense"}) is None
    )
    assert ev.check(None, None) is None
    assert "no transaction" in ev.check({"amount": 5}, None)
    assert "none" in ev.check(None, {"amount": 5})
    assert "amount" in ev.check({"amount": 6}, {"amount": 5})
    assert "type" in ev.check({"amount": 5, "type": "income"}, {"amount": 5, "type": "expense"})


def test_cases_are_well_formed_and_cover_every_language() -> None:
    assert {c[0] for c in ev.CASES} == {"pt", "nl", "en", "fr", "de"}
    assert len(ev.CASES) >= 90


def test_cases_have_no_duplicates_and_balance_per_language() -> None:
    keys = [(c[0], c[1].lower()) for c in ev.CASES]
    assert len(keys) == len(set(keys))
    for lang in ("pt", "nl", "en", "fr", "de"):
        assert sum(1 for c in ev.CASES if c[0] == lang) >= 12, lang
    negatives = sum(1 for c in ev.CASES if c[2] is None)
    assert 15 <= negatives <= len(ev.CASES) // 2  # enough "not a transaction" cases, not most


def test_the_other_suites_are_well_formed() -> None:
    assert len(ev.MULTI_CASES) >= 5 and len(ev.ANALYSIS_CASES) >= 6 and len(ev.WORKOUT_CASES) >= 8
    assert any(c[2] is None for c in ev.ANALYSIS_CASES) and any(
        c[2] is None for c in ev.WORKOUT_CASES
    )
    for suite in (ev.ANALYSIS_CASES, ev.WORKOUT_CASES):
        keys = [(c[0], c[1].lower()) for c in suite]
        assert len(keys) == len(set(keys))
        assert {c[0] for c in suite} <= {"pt", "nl", "en", "fr", "de"}


def test_check_subset_pins_only_listed_fields_and_unwraps_the_analysis_spec() -> None:
    assert ev.check_subset({"duration_minutes": 50, "x": 1}, {"duration_minutes": 50}) is None
    assert "duration_minutes" in ev.check_subset({"duration_minutes": 40}, {"duration_minutes": 50})
    spec = {"supported": True, "spec": {"metric": "spent", "period": "this_year"}}
    assert ev.check_subset(spec, {"metric": "spent"}) is None
    assert ev.check_subset({"supported": False}, None) is None
    assert ev.check_subset(None, None) is None
    assert "decline" in ev.check_subset(spec, None)
    assert "none" in ev.check_subset(None, {"metric": "spent"})
    assert ev.check_subset({"distance_km": 5.0}, {"distance_km": 5.0}) is None
