"""An expired or unknown panel link shows a human page, not raw JSON (links now last 7 days)."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Member

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

ASK = {
    "pt": "meu dashboard",
    "nl": "mijn dashboard",
    "en": "my dashboard",
    "fr": "mon tableau de bord",
    "de": "mein dashboard",
}


async def _expired_token(lab, lang: str, v2: bool) -> str:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Member).where(Member.id == lab.member_id).values(language=lang, dashboard_v2=v2)
        )
        m = await s.get(Member, lab.member_id)
        await panel_tokens.ensure_panel_token(s, m)
        m.dashboard_token_created_at = datetime.now(UTC) - timedelta(days=8)
        await s.commit()
        token = str(m.dashboard_token)
    await engine.dispose()
    return token


@db
@pytest.mark.parametrize("lang", list(ASK))
@pytest.mark.parametrize("v2", [False, True])
async def test_expired_link_page_is_html_in_the_members_language(lab, client, lang, v2) -> None:
    token = await _expired_token(lab, lang, v2)
    r = await client.get(f"/d/{token}")
    await engine.dispose()
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert f'lang="{lang}"' in r.text
    assert ASK[lang] in r.text.lower()
    assert token not in r.text


@db
async def test_unknown_link_page_is_html_too(client) -> None:
    for raw in (str(uuid.uuid4()), "not-a-uuid"):
        r = await client.get(f"/d/{raw}")
        assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
        assert raw not in r.text
    await engine.dispose()


@db
async def test_the_api_keeps_answering_json_404(lab, client) -> None:
    token = await _expired_token(lab, "pt", True)
    r = await client.get(f"/api/d/{token}/summary")
    await engine.dispose()
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
