"""Open-my-dashboard button (cta_url) with a plain-text fallback."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from alfred import whatsapp

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


class _Resp:
    def __init__(self, ok: bool) -> None:
        self.is_success, self.status_code, self.text = ok, 200 if ok else 400, "{}"

    def raise_for_status(self) -> None:
        if not self.is_success:
            raise httpx.HTTPStatusError("bad", request=None, response=None)  # type: ignore[arg-type]

    def json(self) -> dict:
        return {"messages": [{"id": "wamid.X"}]}


class _Client:
    def __init__(self, resp, sink) -> None:
        self.resp, self.sink = resp, sink

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        self.sink.append(json)
        return self.resp


async def test_cta_url_payload_shape() -> None:
    sent: list = []
    with patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(True), sent)):
        await whatsapp.send_cta_url(
            "3161", "Aqui está o seu painel.", "Abrir meu painel", "https://x/d/abc"
        )
    p = sent[0]
    assert p["type"] == "interactive" and p["interactive"]["type"] == "cta_url"
    assert p["interactive"]["action"]["name"] == "cta_url"
    assert p["interactive"]["action"]["parameters"] == {
        "display_text": "Abrir meu painel",
        "url": "https://x/d/abc",
    }


async def test_cta_url_truncates_to_the_meta_limits() -> None:
    sent: list = []
    with patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(True), sent)):
        await whatsapp.send_cta_url("3161", "x" * 2000, "y" * 50, "https://x/d/abc")
    i = sent[0]["interactive"]
    assert len(i["body"]["text"]) == 1024
    assert len(i["action"]["parameters"]["display_text"]) == 20


async def test_cta_url_falls_back_to_one_text_message() -> None:
    sent: list = []
    fake_text = AsyncMock(return_value={})
    with (
        patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Client(_Resp(False), sent)),
        patch("alfred.whatsapp.send_text", fake_text),
    ):
        await whatsapp.send_cta_url(
            "3161", "Aqui está o seu painel.", "Abrir meu painel", "https://x/d/abc"
        )
    assert len(sent) == 1  # one interactive attempt, no retry loop
    fake_text.assert_awaited_once()
    assert "https://x/d/abc" in fake_text.await_args.args[1]


async def test_cta_url_network_error_also_falls_back() -> None:
    class _Boom(_Client):
        async def post(self, url, json=None, headers=None):
            raise httpx.ConnectError("down")

    fake_text = AsyncMock(return_value={})
    with (
        patch("alfred.whatsapp.httpx.AsyncClient", lambda **kw: _Boom(_Resp(True), [])),
        patch("alfred.whatsapp.send_text", fake_text),
    ):
        await whatsapp.send_cta_url("3161", "Corpo.", "Abrir", "https://x/d/abc")
    fake_text.assert_awaited_once()


async def test_cta_url_suppressed_sends_nothing() -> None:
    with patch("alfred.whatsapp.delivery.suppressed", return_value=True):
        assert await whatsapp.send_cta_url("3161", "x", "y", "https://z") == {"suppressed": True}


@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
def test_button_text_fits_the_meta_limit(lang) -> None:
    from alfred.conversation import _t

    assert 0 < len(_t("btn_open_panel", lang)) <= 20
    assert "{" not in _t("dashboard_cta", lang)


@db
@pytest.mark.parametrize(
    "phrase",
    ["meu dashboard", "dashboard", "mijn dashboard", "my dashboard", "mon tableau de bord"],
)
async def test_dashboard_command_sends_the_button(lab, monkeypatch, phrase) -> None:
    from alfred.settings import settings

    monkeypatch.setattr(settings, "base_url", "https://alfred.example")
    await lab.say(phrase)
    assert lab.cta, "no button was sent"
    url, text = lab.cta[-1]
    assert url.startswith("https://alfred.example/d/") and text == "Abrir meu painel"
    assert len(lab.sent) == 1


@db
async def test_dashboard_command_without_base_url_sends_text_only(lab, monkeypatch) -> None:
    from alfred.settings import settings

    monkeypatch.setattr(settings, "base_url", "")
    monkeypatch.delenv("BASE_URL", raising=False)
    reply = await lab.say("meu dashboard")
    assert not lab.cta and "painel" in reply.lower()


@db
async def test_dashboard_button_never_carries_the_export_token(lab, monkeypatch) -> None:
    from sqlalchemy import select

    from alfred.models import Member
    from alfred.settings import settings

    monkeypatch.setattr(settings, "base_url", "https://alfred.example")
    await lab.say("meu dashboard")
    m = (await lab.rows(select(Member).where(Member.id == lab.member_id)))[0][0]
    assert str(m.dashboard_token) in lab.cta[-1][0]
    assert m.export_token is None
