"""Hard test script — group G: it starts with "oi" and "sim" so a wiped account is welcomed."""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "scripts" / "hard_test.py"


@pytest.fixture
def script(monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "x")
    spec = importlib.util.spec_from_file_location("hard_test_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_plan_opens_with_oi_and_sim(script, monkeypatch):
    sent = []
    monkeypatch.setattr(script, "send", lambda label, text, run_id, delay=0: sent.append(text))
    steps = script.plano(["A", "K"])
    assert steps[0] is script.abertura and steps[1:] == [script.bloco_a, script.bloco_k]
    steps[0]("run", 0)
    assert sent == ["oi", "sim"]


def test_the_opening_can_be_skipped(script):
    assert script.plano(["A"], sem_abertura=True) == [script.bloco_a]
