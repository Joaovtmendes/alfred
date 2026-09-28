"""Public privacy policy page (/privacy) — required by Meta and the GDPR."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from alfred.legal import POLICY


@pytest.mark.parametrize("lang", ["en", "nl", "pt"])
async def test_privacy_page_renders_each_language(client: AsyncClient, lang: str) -> None:
    resp = await client.get("/privacy", params={"lang": lang})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert POLICY[lang]["title"] in resp.text
    assert f'lang="{lang}"' in resp.text


async def test_privacy_unknown_language_falls_back_to_english(client: AsyncClient) -> None:
    resp = await client.get("/privacy", params={"lang": "xx"})
    assert resp.status_code == 200
    assert POLICY["en"]["title"] in resp.text


def test_every_language_has_the_same_sections() -> None:
    counts = {lang: len(p["sections"]) for lang, p in POLICY.items()}
    assert len(set(counts.values())) == 1, counts
