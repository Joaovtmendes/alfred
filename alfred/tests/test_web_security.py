"""Sprint 4b — headers, no-store, CORS, rate limit, token TTL, audit trail."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from alfred import dashboard
from alfred.db import engine
from alfred.models import AuditLog, Member
from alfred.web_security import RateLimiter, _dashboard_limiter


async def test_security_headers_on_every_response(client) -> None:
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


async def test_dashboard_routes_are_never_cached(client) -> None:
    await engine.dispose()  # this test runs in its own event loop
    for path in (f"/d/{uuid.uuid4()}", f"/api/d/{uuid.uuid4()}/summary"):
        r = await client.get(path)
        assert r.status_code == 404
        assert r.headers["cache-control"] == "no-store"


async def test_no_cors_headers_are_ever_sent(client) -> None:
    r = await client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    pre = await client.options(
        "/health",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in pre.headers


def test_rate_limiter_window() -> None:
    rl = RateLimiter(limit=2, window=60)
    assert rl.allow("a", now=0) and rl.allow("a", now=1)
    assert not rl.allow("a", now=2)
    assert rl.allow("b", now=2)  # other keys are independent
    assert rl.allow("a", now=61)  # window slid


async def test_dashboard_returns_429_when_hammered(client, monkeypatch) -> None:
    monkeypatch.setattr(_dashboard_limiter, "limit", 3)
    await engine.dispose()
    _dashboard_limiter._hits.clear()
    codes = [(await client.get(f"/d/{uuid.uuid4()}")).status_code for _ in range(5)]
    _dashboard_limiter._hits.clear()
    assert codes[:3] == [404, 404, 404] and codes[3:] == [429, 429]


def test_token_expiry() -> None:
    fresh = SimpleNamespace(dashboard_token_created_at=datetime.now(UTC) - timedelta(days=3))
    old = SimpleNamespace(dashboard_token_created_at=datetime.now(UTC) - timedelta(days=8))
    legacy = SimpleNamespace(dashboard_token_created_at=None)
    assert not dashboard.token_expired(fresh)
    assert dashboard.token_expired(old)
    assert not dashboard.token_expired(legacy)


async def test_dashboard_link_is_issued_reused_and_rotated_when_expired(lab) -> None:
    await lab.say("meu dashboard")  # base_url unset in tests → still creates the token
    m = await lab.scalar(select(Member).where(Member.id == lab.member_id))
    first = m.dashboard_token
    assert first and m.dashboard_token_created_at

    await lab.say("meu dashboard")
    m = await lab.scalar(select(Member).where(Member.id == lab.member_id))
    assert m.dashboard_token == first  # still valid → reused

    # age the token past the TTL → next request rotates it
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        mem = await s.get(Member, lab.member_id)
        mem.dashboard_token_created_at = datetime.now(UTC) - timedelta(days=200)
        await s.commit()
    await lab.say("meu dashboard")
    m = await lab.scalar(select(Member).where(Member.id == lab.member_id))
    assert m.dashboard_token != first
    events = [
        r[0]
        for r in await lab.rows(select(AuditLog.event).where(AuditLog.member_id == lab.member_id))
    ]
    assert events.count("dashboard_link_issued") == 1
    assert events.count("dashboard_link_rotated") == 1
