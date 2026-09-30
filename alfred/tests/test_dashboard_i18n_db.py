"""V1-17 — the web dashboard speaks the member's language (page + API)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import update

from alfred import dashboard
from alfred.dashboard_i18n import SUPPORTED, payload, ui
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


@pytest.mark.parametrize(
    ("lang", "cat"), [("nl", "Supermarkt"), ("en", "Groceries"), ("pt", "Supermercado")]
)
async def test_api_categories_are_localised(lab, client, lang: str, cat: str) -> None:
    token = await _prepare(lab, lang)
    r = await client.get(f"/api/d/{token}")
    assert r.status_code == 200
    data = r.json()
    assert data["expenses_by_category"][0]["category"] == cat
    assert data["recent_transactions"][0]["category"] == cat
    await engine.dispose()


@pytest.mark.parametrize("lang", SUPPORTED)
async def test_page_carries_language_and_strings(lab, client, lang: str) -> None:
    token = await _prepare(lab, lang)
    r = await client.get(f"/d/{token}")
    assert r.status_code == 200
    assert f'<html lang="{lang}">' in r.text
    assert ui(lang)["title"] in r.text
    assert "__I18N__" not in r.text and "__LANG__" not in r.text and "__TITLE__" not in r.text
    await engine.dispose()


def test_every_string_exists_in_every_language() -> None:
    for lang in SUPPORTED:
        strings = ui(lang)
        assert all(v.strip() for v in strings.values()), lang
    assert {tuple(sorted(ui(lang))) for lang in SUPPORTED} == {tuple(sorted(ui("pt")))}


def test_payload_is_valid_json_and_script_safe() -> None:
    raw = payload("fr")
    assert "<" not in raw and ">" not in raw
    data = json.loads(raw)
    assert data["lang"] == "fr" and len(data["months"]) == 12 and data["locale"] == "fr-FR"


def test_unknown_language_falls_back_to_portuguese() -> None:
    assert ui("xx") == ui("pt") and ui(None) == ui("pt")


def test_template_has_no_hardcoded_portuguese_labels() -> None:
    html = dashboard._HTML_TEMPLATE
    for frag in (
        "Gastos por categoria",
        "Histórico mensal",
        "Metas<",
        "Você ",
        "Ainda não",
        "A carregar",
    ):
        assert frag not in html, frag
    assert re.search(r"const I18N = __I18N__;", html)
