"""Adversarial regressions for the panel foundation (security audit)."""

from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import text

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member
from alfred.web_security import _dashboard_limiter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    _dashboard_limiter._hits.clear()
    yield
    _dashboard_limiter._hits.clear()


async def _token(lab) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        t = str(m.dashboard_token)
    await engine.dispose()
    return t


@db
@pytest.mark.parametrize(
    "qs",
    [
        "from=9999-12-31&to=9999-12-31",
        "from=9999-12-30&to=9999-12-31",
        "from=0001-01-01&to=0001-01-02",
        "from=0001-01-01&to=0001-01-01",
        "from=1999-12-31&to=2000-01-02",
        "month=0001-01",
        "month=9999-12",
        "from=2026-02-30&to=2026-03-01",
        "categories=" + ",".join(["a"] * 15000),
        "trip=" + "9" * 5000,
        "state=" + "paid," * 5000,
        "from=%00&to=%ff",
        "month=%D9%A2%D9%A0%D9%A2%D9%A6-%D9%A1%D9%A0",
    ],
    ids=lambda q: q[:40],
)
@pytest.mark.parametrize("tab", ["summary", "money", "health", "agenda", "trips"])
async def test_hostile_filters_never_500(lab, client, tab, qs) -> None:
    token = await _token(lab)
    r = await client.get(f"/api/d/{token}/{tab}?{qs}")
    assert r.status_code == 200, (qs[:60], r.status_code, r.text[:200])
    await engine.dispose()


@db
@pytest.mark.parametrize(
    "path",
    [
        "/panel-assets/..%2fpanel_tokens.py",
        "/panel-assets/%2e%2e/panel_tokens.py",
        "/panel-assets/%2e%2e%2f%2e%2e%2fsettings.py",
        "/panel-assets/panel.css%00",
        "/panel-assets/PANEL.CSS",
        "/panel-assets/panel.css/",
        "/panel-assets/shell.html",
        "/panel-assets/__init__.py",
        "/panel-assets/fonts/hanken-grotesk-latin-400-normal.woff2",
        "/panel-assets/..\\panel.css",
        "/panel-assets/%252e%252e%252fpanel.css",
    ],
)
async def test_panel_assets_whitelist(client, path) -> None:
    r = await client.get(path)
    assert r.status_code in (404, 307), (path, r.status_code)
    if r.status_code == 307:  # trailing-slash redirect to the same whitelisted asset: harmless
        assert r.headers["location"].endswith("/panel-assets/panel.css")


@db
@pytest.mark.parametrize(
    "token",
    [
        "%00",
        "{" + "0" * 32 + "}",
        "0" * 40,
        "urn:uuid:" + "a" * 32,
        "a" * 5000,
        "%E2%80%A8",
        "'%20OR%201=1--",
    ],
)
@pytest.mark.parametrize("route", ["/d/{}", "/api/d/{}", "/api/d/{}/summary", "/api/d/{}/export"])
async def test_garbage_tokens_are_plain_404(client, token, route) -> None:
    r = await client.get(route.format(token))
    assert r.status_code == 404
    assert "Traceback" not in r.text and "sqlalchemy" not in r.text.lower()
    r = await client.post(route.format(token))
    assert r.status_code in (404, 405)
    await engine.dispose()


@db
async def test_security_headers_on_every_token_surface(lab, client) -> None:
    token = await _token(lab)
    for path in (f"/d/{token}", f"/api/d/{token}/summary", f"/api/d/{token}/health"):
        r = await client.get(path)
        assert r.headers["cache-control"] == "no-store", path
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        await engine.dispose()
    r = await client.get("/panel-assets/panel.js")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert re.search(r"nonce-", r.headers.get("content-security-policy", "")) is None
    r = await client.get(f"/d/{uuid.uuid4()}")  # even the 404 carries a nonce CSP, no inline
    assert "unsafe-inline" not in r.headers["content-security-policy"]
    await engine.dispose()


@db
async def test_export_over_http_is_spent_by_exactly_one_of_many_racers(lab, client) -> None:
    import asyncio

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        export = str(await panel_tokens.issue_export_token(s, m))
        await s.commit()
    await engine.dispose()
    codes = await asyncio.gather(*(client.post(f"/api/d/{export}/export") for _ in range(8)))
    assert sorted(r.status_code for r in codes) == [200] + [404] * 7
    # replay, and the GET confirmation page of a spent link
    assert (await client.post(f"/api/d/{export}/export")).status_code == 404
    assert (await client.get(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()


@db
async def test_token_variants_of_the_same_uuid_do_not_cross_scopes(lab, client) -> None:
    token = await _token(lab)
    other = str(uuid.uuid4())
    for variant in (token.upper(), token.replace("-", ""), "{" + token + "}"):
        assert (await client.get(f"/api/d/{variant}/export")).status_code == 404
        await engine.dispose()
    assert (await client.get(f"/api/d/{other}/summary")).status_code == 404
    await engine.dispose()


@db
async def test_housemate_data_never_appears_in_my_summary(lab, client) -> None:
    from datetime import UTC, datetime

    from alfred.models import Expense

    async with AsyncSessionLocal() as s:
        mate = Member(
            household_id=lab.household_id,
            wa_phone="3161" + uuid.uuid4().hex[:7],
            display_name="Mate",
            consent_state="accepted",
        )
        s.add(mate)
        await s.flush()
        s.add(
            Expense(
                member_id=mate.id,
                household_id=lab.household_id,
                transaction_type="expense",
                amount=777,
                merchant="secret",
                category="overig",
                status="paid",
                expense_date=datetime.now(UTC),
                trip_id=None,
            )
        )
        await s.commit()
        mate_id = mate.id
    await engine.dispose()
    token = await _token(lab)
    body = (await client.get(f"/api/d/{token}/summary")).json()
    assert body["cards"][0]["values"]["expense"] == 0
    assert (await client.get(f"/api/d/{mate_id}/summary")).status_code == 404  # id is not a token
    await engine.dispose()
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM expense WHERE member_id = :m"), {"m": mate_id})
        await s.execute(text("DELETE FROM member WHERE id = :m"), {"m": mate_id})
        await s.commit()
    await engine.dispose()


def test_sentry_scrub_removes_dashboard_and_export_tokens_from_the_url() -> None:
    from alfred.observability import _scrub

    token = str(uuid.uuid4())
    event = {
        "request": {"url": f"https://a.example/api/d/{token}/export", "method": "POST"},
        "transaction": f"/d/{token}",
        "breadcrumbs": {"values": [{"message": f"POST /d/{token} 200", "category": "httplib"}]},
        "exception": {"values": [{"value": f"boom at /api/d/{token}/summary"}]},
    }
    out = str(_scrub(event, {}))
    assert token not in out
