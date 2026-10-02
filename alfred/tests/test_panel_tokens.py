from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import AuditLog, Member

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _m(age_days: float | None):
    created = None if age_days is None else NOW - timedelta(days=age_days)
    return SimpleNamespace(dashboard_token_created_at=created)


@pytest.mark.parametrize(
    ("age", "expired", "renew"),
    [
        (0, False, False),
        (3, False, False),  # 4 of 7 days left: more than half
        (3.6, False, True),  # 3.4 days left: less than half
        (6.9, False, True),
        (7.1, True, True),
        (None, False, False),  # legacy NULL timestamp is never "expired"
    ],
)
def test_expiry_and_renewal_thresholds(age, expired, renew) -> None:
    m = _m(age)
    assert panel_tokens.token_expired(m, NOW) is expired
    assert panel_tokens.needs_renewal(m, NOW) is renew


@db
async def test_issue_reuse_and_rotate(lab) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        assert await panel_tokens.ensure_panel_token(s, m, NOW) is True  # issued
        first = m.dashboard_token
        assert await panel_tokens.ensure_panel_token(s, m, NOW + timedelta(days=1)) is False
        assert m.dashboard_token == first  # still more than half left
        assert await panel_tokens.ensure_panel_token(s, m, NOW + timedelta(days=4)) is True
        assert m.dashboard_token != first  # less than half left: reissued
        await s.commit()
        events = (
            (await s.execute(select(AuditLog.event).where(AuditLog.member_id == lab.member_id)))
            .scalars()
            .all()
        )
        assert "dashboard_link_issued" in events and "dashboard_link_rotated" in events
    await engine.dispose()


@db
async def test_rotated_token_stops_working(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        old = str(m.dashboard_token)
        m.dashboard_token_created_at = datetime.now(UTC) - timedelta(days=5)  # < half left
        await panel_tokens.ensure_panel_token(s, m)
        new = str(m.dashboard_token)
        await s.commit()
    await engine.dispose()
    assert (await client.get(f"/d/{old}")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/d/{new}")).status_code == 200
    assert uuid.UUID(old) != uuid.UUID(new)
    await engine.dispose()


@db
async def test_two_concurrent_requests_hand_out_the_same_renewed_link(lab) -> None:
    """Two "meu painel" at once: the second must see the first one's new token, not rotate again
    (otherwise the link the member received first is already dead)."""
    import asyncio

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        m.dashboard_token_created_at = datetime.now(UTC) - timedelta(days=5)  # < half left
        await s.commit()

    first, second = AsyncSessionLocal(), AsyncSessionLocal()
    try:
        m1, m2 = await first.get(Member, lab.member_id), await second.get(Member, lab.member_id)
        assert await panel_tokens.ensure_panel_token(first, m1) is True  # rotates, uncommitted

        async def other() -> bool:
            return await panel_tokens.ensure_panel_token(second, m2)

        task = asyncio.create_task(other())
        await asyncio.sleep(0.3)  # let it reach the row lock
        await first.commit()
        assert await asyncio.wait_for(task, 5) is False  # saw the fresh token: no second rotation
        assert m2.dashboard_token == m1.dashboard_token
        await second.commit()
    finally:
        await first.close()
        await second.close()
    await engine.dispose()
