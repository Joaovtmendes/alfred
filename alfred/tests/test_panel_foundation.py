"""Dashboard v2 foundation: settings, columns and the migration chain."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member
from alfred.settings import Settings

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def test_token_defaults_follow_the_spec() -> None:
    s = Settings(_env_file=None)
    assert s.dashboard_token_ttl_days == 7
    assert s.export_token_ttl_minutes == 15


@db
async def test_new_member_has_no_export_token(lab) -> None:
    async with AsyncSessionLocal() as s:
        m = (await s.execute(select(Member).where(Member.id == lab.member_id))).scalar_one()
        assert m.export_token is None and m.export_token_expires_at is None
    await engine.dispose()
