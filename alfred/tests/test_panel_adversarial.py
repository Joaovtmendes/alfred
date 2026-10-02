"""Adversarial regressions for the panel foundation (security audit)."""

from __future__ import annotations

import os
import re
import uuid

import pytest

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
