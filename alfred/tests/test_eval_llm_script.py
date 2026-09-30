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
