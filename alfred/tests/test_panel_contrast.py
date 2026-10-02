"""WCAG AA contrast of the panel colour tokens, both themes."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

spec = importlib.util.spec_from_file_location(
    "contrast_check", pathlib.Path(__file__).parent.parent / "scripts" / "contrast_check.py"
)
cc = importlib.util.module_from_spec(spec)
sys.modules["contrast_check"] = cc
spec.loader.exec_module(cc)


def test_ratio_known_values() -> None:
    assert round(cc.ratio("#000000", "#FFFFFF"), 1) == 21.0
    assert cc.ratio("#777777", "#777777") == 1.0


def test_both_themes_are_read_from_the_css() -> None:
    themes = cc._themes()
    assert set(themes) == {"dark", "light", "dark-media-light"}
    assert themes["dark"]["bg"].lower() == "#0d1117" and themes["light"]["bg"].lower() == "#f3f5f8"
    # the prefers-color-scheme copy of the light theme must not drift from [data-theme=light]
    assert themes["dark-media-light"] == themes["light"]


def test_every_pair_passes_aa_in_both_themes() -> None:
    failures = cc.check()
    assert failures == [], failures
