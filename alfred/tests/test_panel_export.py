from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member
from alfred.settings import settings

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _tokens(lab) -> tuple[str, str]:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        export = await panel_tokens.issue_export_token(s, m)
        await s.commit()
        panel = str(m.dashboard_token)
    await engine.dispose()
    return panel, str(export)


@db
async def test_scopes_do_not_cross(lab, client) -> None:
    panel, export = await _tokens(lab)
    assert (await client.get(f"/api/d/{panel}/export")).status_code == 404
    await engine.dispose()
    assert (await client.post(f"/api/d/{panel}/export")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/d/{export}")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}")).status_code == 404
    await engine.dispose()


@db
async def test_get_confirms_without_spending_and_post_downloads_once(lab, client) -> None:
    _, export = await _tokens(lab)
    page = await client.get(f"/api/d/{export}/export")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert page.headers["cache-control"] == "no-store"
    assert '<form method="post">' in page.text
    assert "form-action" not in page.headers["content-security-policy"]  # the POST must be allowed
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}/export")).status_code == 200  # still valid
    await engine.dispose()
    first = await client.post(f"/api/d/{export}/export")
    assert first.status_code == 200
    assert "attachment" in first.headers["content-disposition"]
    body = first.json()
    assert body["member_id"] == str(lab.member_id)
    member_row = body["tables"]["member"][0]
    assert "dashboard_token" not in member_row and "export_token" not in member_row
    await engine.dispose()
    assert (await client.post(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()


@db
async def test_expired_export_token_is_404(lab, client) -> None:
    _, export = await _tokens(lab)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Member)
            .where(Member.id == lab.member_id)
            .values(export_token_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    await engine.dispose()
    assert (await client.get(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()
    assert (await client.post(f"/api/d/{export}/export")).status_code == 404
    await engine.dispose()


@db
async def test_a_new_export_token_replaces_the_previous_one(lab, client) -> None:
    _, first = await _tokens(lab)
    _, second = await _tokens(lab)
    assert first != second
    assert (await client.post(f"/api/d/{first}/export")).status_code == 404
    await engine.dispose()
    assert (await client.post(f"/api/d/{second}/export")).status_code == 200
    await engine.dispose()


@db
async def test_garbage_export_token_is_404(client) -> None:
    assert (await client.get("/api/d/not-a-uuid/export")).status_code == 404
    assert (await client.post("/api/d/not-a-uuid/export")).status_code == 404
    await engine.dispose()


@db
async def test_concurrent_consumption_lets_only_one_through(lab) -> None:
    _, export = await _tokens(lab)

    async def attempt() -> bool:
        async with AsyncSessionLocal() as s:
            got = await panel_tokens.consume_export_token(s, export)
            await s.commit()
            return got is not None

    results = await asyncio.gather(*(attempt() for _ in range(5)))
    assert results.count(True) == 1
    await engine.dispose()


@db
async def test_export_command_issues_an_export_token(lab, monkeypatch) -> None:
    monkeypatch.setattr(settings, "base_url", "https://alfred.example")
    reply = await lab.say("exportar meus dados")
    assert "https://alfred.example/api/d/" in reply and reply.rstrip().endswith("/export")
    assert "15" in reply
    token = reply.rsplit("/api/d/", 1)[1].split("/export")[0]
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        assert str(m.export_token) == token
        assert m.dashboard_token is None or str(m.dashboard_token) != token
    await engine.dispose()
