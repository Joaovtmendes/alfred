"""V1-17 — the web dashboard speaks the member's language (page + API)."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from alfred import dashboard, panel_i18n
from alfred.dashboard_i18n import SUPPORTED, ui
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Expense, Member


async def _prepare(lab, lang: str) -> str:
    async with AsyncSessionLocal() as s:
        await s.execute(update(Member).where(Member.id == lab.member_id).values(language=lang))
        s.add(
            Expense(
                member_id=lab.member_id,
                household_id=lab.household_id,
                transaction_type="expense",
                amount=12.5,
                merchant="Jumbo",
                category="supermarkt",
                expense_date=datetime.now(UTC),
            )
        )
        member = await s.get(Member, lab.member_id)
        await dashboard.ensure_dashboard_token(s, member)
        await s.commit()
        token = str(member.dashboard_token)
    await engine.dispose()
    return token


@pytest.mark.parametrize("lang", SUPPORTED)
async def test_page_carries_language_and_strings(lab, client, lang: str) -> None:
    token = await _prepare(lab, lang)
    r = await client.get(f"/d/{token}")
    assert r.status_code == 200
    assert f'<html lang="{lang}"' in r.text
    labels = re.findall(r'role="tab"[^>]*>([^<]+)<', r.text)
    assert labels == [t["label"] for t in panel_i18n.tabs(lang)]
    assert not re.search(r"__[A-Z_]+__", r.text)
    await engine.dispose()


def test_every_string_exists_in_every_language() -> None:
    for lang in SUPPORTED:
        strings = ui(lang)
        assert all(v.strip() for v in strings.values()), lang
    assert {tuple(sorted(ui(lang))) for lang in SUPPORTED} == {tuple(sorted(ui("pt")))}


def test_unknown_language_falls_back_to_portuguese() -> None:
    assert ui("xx") == ui("pt") and ui(None) == ui("pt")
