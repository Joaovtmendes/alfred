"""The template files that scripts/submit_templates.py sends to Meta stay well-formed."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DIR = Path(__file__).resolve().parent.parent / "m5-templates"
FILES = sorted(DIR.glob("*.json"))
LANGS = {"en", "pt_BR", "nl", "fr", "de"}
V2_TEMPLATES = {"alfred_payment_reminder", "alfred_monthly_summary", "alfred_appointment_reminder"}


def test_the_three_v2_templates_are_present() -> None:
    assert V2_TEMPLATES <= {p.stem for p in FILES}


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_template_file_is_valid(path: Path) -> None:
    spec = json.loads(path.read_text())
    assert spec["name"] == path.stem and spec["category"] in ("UTILITY", "MARKETING")
    assert re.fullmatch(r"[a-z0-9_]+", spec["name"])
    trans = spec["translations"]
    assert set(trans) == LANGS
    for lang, t in trans.items():
        assert t["language"] == lang
        body = next(c for c in t["components"] if c["type"] == "BODY")
        assert len(body["text"]) <= 1024
        variables = re.findall(r"\{\{(\d+)\}\}", body["text"])
        if variables:  # Meta: numbered in order, with a sample, never at the very start or end
            assert variables == [str(i) for i in range(1, len(variables) + 1)]
            assert len(body["example"]["body_text"][0]) == len(variables)
            assert not body["text"].startswith("{{") and not body["text"].rstrip().endswith("}}")
        for c in t["components"]:
            if c["type"] == "HEADER":
                assert len(c["text"]) <= 60
            if c["type"] == "FOOTER":
                assert len(c["text"]) <= 60


@pytest.mark.parametrize("name", sorted(V2_TEMPLATES))
def test_v2_templates_take_exactly_one_single_line_variable(name: str) -> None:
    spec = json.loads((DIR / f"{name}.json").read_text())
    for lang, t in spec["translations"].items():
        body = next(c for c in t["components"] if c["type"] == "BODY")
        assert body["text"].count("{{1}}") == 1 and "{{2}}" not in body["text"], lang
        sample = body["example"]["body_text"][0][0]
        assert "\n" not in sample and "  " not in sample, lang


def test_cron_collapses_whitespace_in_template_parameters() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).resolve().parent.parent / "scripts" / "daily_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._one_line("a\n\nb\t c   d") == "a b c d"
