from __future__ import annotations

import os

import pytest
from sqlalchemy import func, select

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member
from alfred.privacy import erase_member

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@db
async def test_erase_kills_panel_and_export_tokens_in_one_transaction(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        export = str(await panel_tokens.issue_export_token(s, m))
        panel = str(m.dashboard_token)
        await s.commit()
    await engine.dispose()

    async with AsyncSessionLocal() as s:  # rollback keeps everything valid
        m = await s.get(Member, lab.member_id)
        await erase_member(s, m)
        await s.rollback()
    await engine.dispose()
    assert (await client.get(f"/d/{panel}")).status_code == 200
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}/export")).status_code == 200
    await engine.dispose()

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await erase_member(s, m)
        await s.commit()
    await engine.dispose()
    assert (await client.get(f"/d/{panel}")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/api/d/{panel}")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()
    assert (await client.post(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()
    async with AsyncSessionLocal() as s:
        left = await s.scalar(
            select(func.count()).select_from(Member).where(Member.id == lab.member_id)
        )
    assert left == 0
    await engine.dispose()
