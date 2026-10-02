"""Demo member: idempotent seed that reproduces the approved mockup numbers."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal, engine
from alfred.models import Budget, Expense, Member
from alfred.web_security import _dashboard_limiter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
spec = importlib.util.spec_from_file_location(
    "seed_demo_member", pathlib.Path(__file__).parent.parent / "scripts" / "seed_demo_member.py"
)
seed = importlib.util.module_from_spec(spec)
sys.modules["seed_demo_member"] = seed
spec.loader.exec_module(seed)


@pytest.fixture(autouse=True)
async def _clean_demo():
    """The demo member must not outlive the test: other DB tests count rows globally."""
    if os.environ.get("ALFRED_TEST_DB") == "1":
        await seed.purge()
    yield
    if os.environ.get("ALFRED_TEST_DB") == "1":
        await seed.purge()


@db
async def test_seed_is_idempotent_and_matches_the_mockup(client) -> None:
    _dashboard_limiter._hits.clear()
    token = await seed.run()
    again = await seed.run()
    assert token and token == again  # same member, same link
    await engine.dispose()
    body = (await client.get(f"/api/d/{again}/summary?month=2026-10")).json()
    card = next(c for c in body["cards"] if c["id"] == "balance")
    assert card["values"] == {"income": 3840.0, "expense": 1997.7, "balance": 1842.3}
    await engine.dispose()
    async with AsyncSessionLocal() as s:
        m = (await s.execute(select(Member).where(Member.wa_phone == seed.DEMO_PHONE))).scalar_one()
        assert m.dashboard_v2 is True
        # every proactive sender (cron reminders, weekly/monthly summaries) selects consent
        # "accepted" only: the demo member must never be messaged at its fake number
        assert m.consent_state == "pending"
        n = (
            await s.execute(
                select(func.count()).select_from(Expense).where(Expense.member_id == m.id)
            )
        ).scalar_one()
        assert n == seed.EXPECTED_ROWS  # a second run did not duplicate anything
        budgets = (
            await s.execute(
                select(func.count()).select_from(Budget).where(Budget.member_id == m.id)
            )
        ).scalar_one()
        assert budgets == 1
    await engine.dispose()


@db
async def test_seed_never_touches_other_members() -> None:
    await seed.run()
    async with AsyncSessionLocal() as s:
        others = (
            await s.execute(
                select(func.count()).select_from(Member).where(Member.wa_phone != seed.DEMO_PHONE)
            )
        ).scalar_one()
    await engine.dispose()
    await seed.run()
    async with AsyncSessionLocal() as s:
        after = (
            await s.execute(
                select(func.count()).select_from(Member).where(Member.wa_phone != seed.DEMO_PHONE)
            )
        ).scalar_one()
    await engine.dispose()
    assert after == others
