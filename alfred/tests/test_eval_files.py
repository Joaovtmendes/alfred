"""The file-reading evaluation set (scripts/eval_llm.py --suite file) renders and checks correctly.

The model is never called here: this only protects the harness, so a real run is meaningful.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "eval_llm", pathlib.Path(__file__).resolve().parent.parent / "scripts" / "eval_llm.py"
)
ev = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ev)


@pytest.mark.parametrize(("name", "lines", "form", "expected"), ev.FILE_CASES)
def test_every_case_renders_a_real_image_or_pdf(name, lines, form, expected) -> None:
    data, mime = ev.render_file(lines, form)
    assert len(data) < 5_000_000
    if form == "pdf":
        assert mime == "application/pdf" and data.startswith(b"%PDF")
    else:
        assert mime == "image/jpeg" and data.startswith(b"\xff\xd8\xff")
    assert expected["kind"] in ("receipt", "service_invoice", "workout_plan", "other")


def test_the_cases_cover_both_kinds_both_formats_and_the_traps() -> None:
    kinds = {c[3]["kind"] for c in ev.FILE_CASES}
    forms = {c[2] for c in ev.FILE_CASES}
    names = " ".join(c[0] for c in ev.FILE_CASES)
    assert kinds == {"receipt", "service_invoice", "workout_plan", "other"}
    assert forms == {"image", "pdf"}
    assert "usd" in names and "injection" in names and "no-weekdays" in names


def test_check_file_accepts_a_good_reading_and_names_the_failure() -> None:
    receipt = {"kind": "receipt", "total": 10.53, "currency": "EUR", "contains": ["jumbo"]}
    assert (
        ev.check_file(
            {"kind": "receipt", "total": "10,53", "currency": "EUR", "merchant": "Jumbo"}, receipt
        )
        is None
    )
    assert "total" in ev.check_file({"kind": "receipt", "total": 11, "merchant": "Jumbo"}, receipt)
    assert "kind" in ev.check_file({"kind": "other"}, receipt)
    assert ev.check_file(None, receipt) == "no reading"
    assert "merchant" in ev.check_file(
        {"kind": "receipt", "total": 10.53, "merchant": "Lidl"}, receipt
    )
    usd = {"kind": "receipt", "total": 12.5, "currency": "USD"}
    assert "currency" in ev.check_file({"kind": "receipt", "total": 12.5, "currency": "EUR"}, usd)


def test_check_file_for_plans() -> None:
    plan = {"kind": "workout_plan", "days": 1, "weekdays": None, "contains": ["agacha"]}
    good = {
        "kind": "workout_plan",
        "days": [{"weekday": None, "items": [{"exercise": "Agachamento"}]}],
    }
    assert ev.check_file(good, plan) is None
    invented = {
        "kind": "workout_plan",
        "days": [{"weekday": "monday", "items": [{"exercise": "Agachamento"}]}],
    }
    assert "invented" in ev.check_file(invented, plan)
    assert "days" in ev.check_file({"kind": "workout_plan", "days": []}, plan)
