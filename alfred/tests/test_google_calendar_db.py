# ruff: noqa: E501
"""Google Calendar: connect link, OAuth callback, appointment mirror, disconnect and erasure.

Google is never called: ``google_calendar._client`` is swapped for a mock transport.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select

from alfred import google_calendar as gcal
from alfred.db import AsyncSessionLocal
from alfred.main import app
from alfred.models import Appointment, GoogleConnection, Member
from alfred.privacy import erase_member, export_member_data
from alfred.settings import settings

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.fixture
def google(monkeypatch):
    """Configured client + a recorder standing in for Google. ``g.responses`` can be overridden."""
    monkeypatch.setattr(settings, "google_client_id", "cid.apps.test")
    monkeypatch.setattr(settings, "google_client_secret", SecretStr("csecret"))
    monkeypatch.setattr(settings, "base_url", "https://app.test")
    monkeypatch.setattr(settings, "google_redirect_uri", "")

    class G:
        calls: list[httpx.Request] = []
        token_status = 200
        token_body: dict = {"access_token": "at-1"}
        api_status = 200
        event_id = "evt-1"

    g = G()
    g.calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        g.calls.append(request)
        url = str(request.url)
        if url.startswith(gcal.TOKEN_URL):
            return httpx.Response(g.token_status, json=g.token_body)
        if url.startswith(gcal.REVOKE_URL):
            return httpx.Response(200, json={})
        if request.method == "POST":
            return httpx.Response(g.api_status, json={"id": g.event_id})
        return httpx.Response(g.api_status if g.api_status != 200 else 204, json={})

    monkeypatch.setattr(
        gcal, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    monkeypatch.setattr(gcal, "send_text", AsyncMock())
    return g


def _calls(g, prefix: str, method: str | None = None) -> list[httpx.Request]:
    return [
        r
        for r in g.calls
        if str(r.url).startswith(prefix) and (method is None or r.method == method)
    ]


async def _connect(lab, refresh: str = "rt-secret") -> None:
    await lab.add(
        GoogleConnection(member_id=lab.member_id, refresh_token=refresh, scope=gcal.SCOPE)
    )


# ── pure: state and phrases ────────────────────────────────────────────────────────────────────


def test_state_round_trip_tamper_and_expiry(google):
    mid = uuid.uuid4()
    state = gcal.make_state(mid)
    assert gcal.read_state(state) == mid
    assert gcal.read_state(state[:-1] + ("0" if state[-1] != "0" else "1")) is None
    assert gcal.read_state("nonsense") is None
    assert gcal.read_state(gcal.make_state(mid, now=time.time() - 1000)) is None
    assert gcal.read_state(gcal.make_state(mid, now=time.time() + 3600)) is None


def test_state_needs_a_configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "google_client_secret", SecretStr(""))
    monkeypatch.setattr(settings, "google_client_id", "")
    assert gcal.read_state(f"{uuid.uuid4().hex}.{int(time.time())}.abc") is None


@pytest.mark.parametrize(
    "text",
    [
        "conectar google agenda",
        "conecta o google agenda",
        "ligar minha agenda do google",
        "connect google calendar",
        "Connect my Google Calendar!",
        "koppel google agenda",
        "google agenda koppelen",
        "connecter google agenda",
        "google kalender verbinden",
    ],
)
def test_connect_phrases(text):
    from alfred.parsing import strip_accents

    assert gcal.match_command(strip_accents(text.lower())) == "connect"


@pytest.mark.parametrize(
    "text",
    [
        "desconectar google agenda",
        "desliga o google agenda",
        "disconnect google calendar",
        "ontkoppel google agenda",
        "google agenda ontkoppelen",
        "déconnecter google agenda",
        "google kalender trennen",
    ],
)
def test_disconnect_phrases(text):
    from alfred.parsing import strip_accents

    assert gcal.match_command(strip_accents(text.lower())) == "disconnect"


@pytest.mark.parametrize(
    "text",
    [
        "minha agenda",
        "conecta a luz",
        "dentista amanhã às 14h",
        "google",
        "agenda do google amanhã",
    ],
)
def test_other_phrases_are_not_commands(text):
    from alfred.parsing import strip_accents

    assert gcal.match_command(strip_accents(text.lower())) is None


def test_every_text_exists_in_five_languages():
    from alfred.conversation import _STRINGS

    for key, table in gcal.STRINGS.items():
        assert set(table) >= {"pt", "nl", "en", "fr", "de"}, key
        assert _STRINGS[key] is table


# ── chat commands ───────────────────────────────────────────────────────────────────────────────


@db
async def test_connect_without_configuration_says_it_is_not_available(lab, monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "")
    reply = await lab.say("conectar google agenda")
    assert "ainda não está disponível" in reply
    assert lab.cta == []


@db
async def test_connect_sends_a_link_with_a_signed_state(lab, google):
    reply = await lab.say("conectar google agenda")
    assert "Google Agenda" in reply and "não leio o resto" in reply
    ((url, label),) = lab.cta
    assert url.startswith("https://app.test/google/connect?state=")
    state = parse_qs(urlparse(url).query)["state"][0]
    assert gcal.read_state(state) == lab.member_id
    assert len(label) <= 20


@db
async def test_connect_when_already_connected_sends_no_link(lab, google):
    await _connect(lab)
    reply = await lab.say("connect google calendar")
    assert "já está ligado" in reply or "already connected" in reply
    assert lab.cta == []


@db
async def test_disconnect_revokes_and_deletes_the_token(lab, google):
    await _connect(lab, "rt-to-revoke")
    reply = await lab.say("desconectar google agenda")
    assert "desligado" in reply
    assert (
        await lab.scalar(
            select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
        )
        is None
    )
    (revoke,) = _calls(google, gcal.REVOKE_URL)
    assert parse_qs(revoke.content.decode())["token"] == ["rt-to-revoke"]


@db
async def test_disconnect_when_not_connected(lab, google):
    assert "não está ligado" in await lab.say("desconectar google agenda")
    assert _calls(google, gcal.REVOKE_URL) == []


# ── web routes ──────────────────────────────────────────────────────────────────────────────────


async def _get(path: str, **params):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.get(path, params=params, follow_redirects=False)


@db
async def test_connect_route_redirects_to_google_with_least_privilege(lab, google):
    state = gcal.make_state(lab.member_id)
    resp = await _get("/google/connect", state=state)
    assert resp.status_code == 302
    loc = urlparse(resp.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "accounts.google.com"
    assert q["scope"] == [gcal.SCOPE]  # calendar.events only: no profile, no e-mail
    assert q["access_type"] == ["offline"] and q["state"] == [state]
    assert q["redirect_uri"] == ["https://app.test/google/callback"]
    assert resp.headers["cache-control"] == "no-store"


@db
async def test_connect_route_rejects_a_bad_state(google):
    assert (await _get("/google/connect", state="nope")).status_code == 410
    assert (await _get("/google/connect")).status_code == 410


@db
async def test_callback_stores_the_refresh_token_and_confirms(lab, google):
    google.token_body = {"access_token": "at", "refresh_token": "rt-new", "scope": gcal.SCOPE}
    resp = await _get("/google/callback", state=gcal.make_state(lab.member_id), code="abc")
    assert resp.status_code == 200 and "Google Agenda ligado" in resp.text
    conn = await lab.scalar(
        select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
    )
    assert conn.refresh_token == "rt-new" and conn.scope == gcal.SCOPE
    exchange = parse_qs(_calls(google, gcal.TOKEN_URL)[0].content.decode())
    assert exchange["code"] == ["abc"] and exchange["grant_type"] == ["authorization_code"]
    gcal.send_text.assert_awaited_once()
    # connecting again replaces the token instead of adding a second row
    google.token_body = {"access_token": "at", "refresh_token": "rt-2", "scope": gcal.SCOPE}
    await _get("/google/callback", state=gcal.make_state(lab.member_id), code="def")
    rows = await lab.rows(
        select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
    )
    assert len(rows) == 1 and rows[0][0].refresh_token == "rt-2"


@db
async def test_callback_refused_by_the_member_keeps_nothing(lab, google):
    resp = await _get(
        "/google/callback", state=gcal.make_state(lab.member_id), error="access_denied"
    )
    assert resp.status_code == 200 and "Nada foi ligado" in resp.text
    assert await lab.scalar(select(GoogleConnection)) is None or True
    assert (
        await lab.scalar(
            select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
        )
        is None
    )
    assert _calls(google, gcal.TOKEN_URL) == []


@db
async def test_callback_without_the_calendar_permission_revokes_and_keeps_nothing(lab, google):
    google.token_body = {"access_token": "at", "refresh_token": "rt", "scope": "openid"}
    resp = await _get("/google/callback", state=gcal.make_state(lab.member_id), code="abc")
    assert "Nada foi ligado" in resp.text
    assert (
        await lab.scalar(
            select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
        )
        is None
    )
    assert len(_calls(google, gcal.REVOKE_URL)) == 2  # refresh and access token


@db
async def test_callback_with_a_forged_state_is_refused(lab, google):
    resp = await _get(
        "/google/callback", state=f"{lab.member_id.hex}.{int(time.time())}.forged", code="x"
    )
    assert resp.status_code == 410
    assert _calls(google, gcal.TOKEN_URL) == []


# ── the mirror ──────────────────────────────────────────────────────────────────────────────────


async def _appt(lab) -> Appointment:
    return (await lab.rows(select(Appointment).where(Appointment.member_id == lab.member_id)))[0][0]


@db
async def test_a_new_appointment_is_mirrored_to_google(lab, google):
    await _connect(lab)
    await lab.say("dentista amanhã às 14h")
    (post,) = _calls(google, gcal.EVENTS_URL, "POST")
    body = json.loads(post.content)
    assert body["summary"] == "Dentista"
    assert (
        body["start"]["timeZone"] == settings.timezone and "T14:00:00" in body["start"]["dateTime"]
    )
    assert body["reminders"]["useDefault"] is False
    assert post.headers["authorization"] == "Bearer at-1"
    assert (await _appt(lab)).google_event_id == "evt-1"


@db
async def test_without_a_connection_nothing_is_sent_to_google(lab, google):
    await lab.say("dentista amanhã às 14h")
    assert google.calls == []
    assert (await _appt(lab)).google_event_id is None


@db
async def test_moving_and_cancelling_follow_in_google(lab, google):
    await _connect(lab)
    await lab.say("dentista amanhã às 14h")
    await lab.say("muda o dentista para depois de amanhã às 10h")
    (patch,) = _calls(google, f"{gcal.EVENTS_URL}/evt-1", "PATCH")
    assert "T10:00:00" in json.loads(patch.content)["start"]["dateTime"]
    assert "cancelado" in await lab.say("cancela o dentista")
    assert len(_calls(google, f"{gcal.EVENTS_URL}/evt-1", "DELETE")) == 1
    assert (await _appt(lab)).google_event_id is None


@db
async def test_undo_button_deletes_the_google_event(lab, google):
    await _connect(lab)
    await lab.say("dentista amanhã às 14h")
    appt = await _appt(lab)
    await lab.tap(f"appt_undo:{appt.id}")
    assert len(_calls(google, f"{gcal.EVENTS_URL}/evt-1", "DELETE")) == 1


@db
async def test_google_failing_never_breaks_the_chat(lab, google):
    await _connect(lab)
    google.api_status = 500
    reply = await lab.say("dentista amanhã às 14h")
    assert "Dentista" in reply
    assert (await _appt(lab)).google_event_id is None


@db
async def test_a_lost_grant_removes_the_connection(lab, google):
    await _connect(lab)
    google.token_status, google.token_body = 400, {"error": "invalid_grant"}
    reply = await lab.say("dentista amanhã às 14h")
    assert "Dentista" in reply
    assert (
        await lab.scalar(
            select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
        )
        is None
    )
    assert _calls(google, gcal.EVENTS_URL) == []


# ── privacy ─────────────────────────────────────────────────────────────────────────────────────


@db
async def test_export_never_contains_the_token_and_erasure_revokes_it(lab, google):
    await _connect(lab, "rt-never-exported")
    async with AsyncSessionLocal() as s:
        data = await export_member_data(s, await s.get(Member, lab.member_id))
    assert "rt-never-exported" not in json.dumps(data, default=str)
    assert "refresh_token" not in json.dumps(data["tables"].get("google_connection", []))
    async with AsyncSessionLocal() as s:
        await erase_member(s, await s.get(Member, lab.member_id))
        await s.commit()
    (revoke,) = _calls(google, gcal.REVOKE_URL)
    assert parse_qs(revoke.content.decode())["token"] == ["rt-never-exported"]
    assert (
        await lab.scalar(
            select(GoogleConnection).where(GoogleConnection.member_id == lab.member_id)
        )
        is None
    )
