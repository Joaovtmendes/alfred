# ruff: noqa: E501
"""The mandatory sign-up page: /cadastro/{token}. One account per number, nothing before it."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select, update

from alfred.db import AsyncSessionLocal, engine
from alfred.legal import POLICY_VERSION
from alfred.models import AuditLog, CalendarOptIn, Household, Member

pytestmark = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

CTA = "alfred.signup.send_cta_url"
TEXT = "alfred.signup.send_text"


@pytest.fixture(autouse=True)
async def _fresh_engine():
    yield
    await engine.dispose()


async def _new_member(state: str = "pending_signup", minutes_left: int = 30, lang: str = "pt"):
    """A number waiting for its sign-up, with a live token. Returns (member_id, token, phone)."""
    phone = "3161" + uuid.uuid4().hex[:7]
    token = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        hh = Household(name="signup")
        s.add(hh)
        await s.flush()
        m = Member(
            household_id=hh.id,
            wa_phone=phone,
            consent_state=state,
            language=lang,
            signup_token=token,
            signup_token_expires_at=datetime.now(UTC) + timedelta(minutes=minutes_left),
        )
        s.add(m)
        await s.commit()
        return m.id, token, phone


async def _load(member_id: uuid.UUID) -> Member:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Member).where(Member.id == member_id))).scalar_one()


async def _cleanup(*ids: uuid.UUID) -> None:
    async with AsyncSessionLocal() as s:
        for mid in ids:
            m = (await s.execute(select(Member).where(Member.id == mid))).scalar_one_or_none()
            if m is None:
                continue
            hh = m.household_id
            await s.execute(delete(CalendarOptIn).where(CalendarOptIn.member_id == mid))
            await s.execute(delete(AuditLog).where(AuditLog.member_id == mid))
            await s.delete(m)
            await s.flush()
            await s.execute(delete(Household).where(Household.id == hh))
        await s.commit()


def _form(**kw) -> dict:
    data = {"lang": "pt", "name": "Maria", "privacy": "1"}
    data.update(kw)
    return {k: v for k, v in data.items() if v is not None}


async def test_the_page_shows_the_form_in_the_members_language(client) -> None:
    mid, token, _ = await _new_member(lang="nl")
    resp = await client.get(f"/cadastro/{token}")
    assert resp.status_code == 200
    assert "Account maken bij Alfred" in resp.text
    assert 'name="privacy"' in resp.text and 'name="health"' in resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert "noindex" in resp.text
    other = await client.get(f"/cadastro/{token}?lang=de")
    assert "Konto anlegen" in other.text
    await _cleanup(mid)


async def test_an_unknown_or_malformed_link_says_it_is_gone(client) -> None:
    for t in (str(uuid.uuid4()), "not-a-token", "../../etc/passwd"):
        resp = await client.get(f"/cadastro/{t}")
        assert resp.status_code == 404
        assert "<form" not in resp.text


async def test_an_expired_link_is_gone_even_with_a_valid_form(client) -> None:
    mid, token, _ = await _new_member(minutes_left=-1)
    assert (await client.get(f"/cadastro/{token}")).status_code == 404
    resp = await client.post(f"/cadastro/{token}", data=_form())
    assert resp.status_code == 404
    assert (await _load(mid)).consent_state == "pending_signup"
    await _cleanup(mid)


async def test_signup_opens_the_account_and_welcomes_in_the_chat(client) -> None:
    mid, token, phone = await _new_member()
    with patch(TEXT, new_callable=AsyncMock) as text, patch(CTA, new_callable=AsyncMock) as cta:
        resp = await client.post(
            f"/cadastro/{token}", data=_form(name="  Maria   Silva ", health="1", deadlines="1")
        )
    assert resp.status_code == 200 and "Conta criada, Maria Silva" in resp.text
    m = await _load(mid)
    assert m.consent_state == "accepted"
    assert m.preferred_name == "Maria Silva" and m.language == "pt"
    assert m.disclosure_version == POLICY_VERSION and m.disclosure_accepted_at is not None
    assert m.health_consent_at is not None
    assert m.signup_token is None and m.signup_token_expires_at is None  # single use
    assert m.dashboard_token is not None  # its own panel link
    async with AsyncSessionLocal() as s:
        assert await s.scalar(select(CalendarOptIn.id).where(CalendarOptIn.member_id == mid))
        events = (
            (await s.execute(select(AuditLog.event).where(AuditLog.member_id == mid)))
            .scalars()
            .all()
        )
    assert "signup_completed" in events
    assert text.await_args.args[0] == phone and "Maria Silva" in text.await_args.args[1]
    cta.assert_awaited_once()
    assert f"/d/{m.dashboard_token}" in cta.await_args.args[3]
    await _cleanup(mid)


async def test_health_and_deadlines_are_optional_and_stay_off(client) -> None:
    mid, token, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock), patch(CTA, new_callable=AsyncMock):
        resp = await client.post(f"/cadastro/{token}", data=_form())
    assert resp.status_code == 200
    m = await _load(mid)
    assert m.health_consent_at is None
    async with AsyncSessionLocal() as s:
        assert (
            await s.scalar(select(CalendarOptIn.id).where(CalendarOptIn.member_id == mid)) is None
        )
    await _cleanup(mid)


async def test_the_privacy_box_is_required(client) -> None:
    mid, token, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock) as text:
        resp = await client.post(f"/cadastro/{token}", data=_form(privacy=None, health="1"))
    assert resp.status_code == 400 and "aviso de privacidade" in resp.text
    assert 'name="health" value="1" checked' in resp.text  # what was typed is kept
    m = await _load(mid)
    assert m.consent_state == "pending_signup" and m.signup_token == token
    text.assert_not_awaited()
    await _cleanup(mid)


@pytest.mark.parametrize(
    "name", ["", "   ", "x" * 41, "<script>alert(1)</script>", "Ana; DROP TABLE member"]
)
async def test_a_bad_name_is_refused(client, name: str) -> None:
    mid, token, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock):
        resp = await client.post(f"/cadastro/{token}", data=_form(name=name))
    assert resp.status_code == 400 and "<script>alert" not in resp.text
    assert (await _load(mid)).consent_state == "pending_signup"
    await _cleanup(mid)


async def test_the_link_works_once(client) -> None:
    mid, token, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock) as text, patch(CTA, new_callable=AsyncMock):
        first = await client.post(f"/cadastro/{token}", data=_form())
        second = await client.post(f"/cadastro/{token}", data=_form(name="Outro"))
    assert first.status_code == 200 and second.status_code == 404
    assert (await _load(mid)).preferred_name == "Maria"
    assert text.await_count == 1
    await _cleanup(mid)


async def test_a_failed_welcome_does_not_undo_the_account(client) -> None:
    mid, token, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock, side_effect=RuntimeError("window closed")):
        resp = await client.post(f"/cadastro/{token}", data=_form())
    assert resp.status_code == 200
    assert (await _load(mid)).consent_state == "accepted"
    await _cleanup(mid)


async def test_the_language_on_the_page_becomes_the_members_language(client) -> None:
    mid, token, _ = await _new_member(lang="pt")
    with patch(TEXT, new_callable=AsyncMock), patch(CTA, new_callable=AsyncMock):
        resp = await client.post(f"/cadastro/{token}", data=_form(lang="en", name="Sam"))
    assert "Account created, Sam" in resp.text
    assert (await _load(mid)).language == "en"
    await _cleanup(mid)


async def test_two_people_get_separate_accounts_and_panels(client) -> None:
    a, ta, _ = await _new_member()
    b, tb, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock), patch(CTA, new_callable=AsyncMock):
        await client.post(f"/cadastro/{ta}", data=_form(name="Ana"))
        await client.post(f"/cadastro/{tb}", data=_form(name="Bruno", health="1"))
    ma, mb = await _load(a), await _load(b)
    assert ma.household_id != mb.household_id
    assert ma.dashboard_token != mb.dashboard_token
    assert ma.preferred_name == "Ana" and mb.preferred_name == "Bruno"
    assert ma.health_consent_at is None and mb.health_consent_at is not None
    await _cleanup(a, b)


async def test_a_token_belongs_to_one_member_only(client) -> None:
    a, ta, _ = await _new_member()
    b, tb, _ = await _new_member()
    with patch(TEXT, new_callable=AsyncMock), patch(CTA, new_callable=AsyncMock):
        await client.post(f"/cadastro/{ta}", data=_form(name="Ana"))
    assert (await _load(b)).consent_state == "pending_signup"  # Ana's sign-up never touched Bruno
    async with AsyncSessionLocal() as s:
        await s.execute(update(Member).where(Member.id == b).values(signup_token=None))
        await s.commit()
    assert (await client.get(f"/cadastro/{tb}")).status_code == 404
    await _cleanup(a, b)
