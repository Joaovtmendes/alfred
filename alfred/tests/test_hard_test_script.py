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


def test_a_webhook_failure_goes_into_the_report(script, monkeypatch):
    class Resp:
        status_code = 503
        text = "bad gateway"

    monkeypatch.setattr(script.httpx, "post", lambda *a, **k: Resp())
    script._falhas.clear()
    script.send("A07", "gastei 12 no mercado", "run1", delay=0)
    assert len(script._falhas) == 1
    falha = script._falhas[0]
    assert falha["step"].startswith("A07#") and falha["message"] == "gastei 12 no mercado"
    assert "503" in falha["title"] and falha["expected"] == "HTTP 200 do webhook"


def test_a_network_error_is_a_failure_too(script, monkeypatch):
    def boom(*a, **k):
        raise OSError("sem rede")

    monkeypatch.setattr(script.httpx, "post", boom)
    script._falhas.clear()
    script.send("K01", "oi", "run2", delay=0)
    assert "erro de rede" in script._falhas[0]["title"]


def test_a_good_response_is_not_a_failure(script, monkeypatch):
    class Resp:
        status_code = 200
        text = "ok"

    monkeypatch.setattr(script.httpx, "post", lambda *a, **k: Resp())
    script._falhas.clear()
    script.send("A01", "oi", "run3", delay=0)
    assert script._falhas == []
