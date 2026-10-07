# ruff: noqa: E501
"""V2-27 part 2 — the "Casa" tab: contracts and monthly cost for everyone."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal
from alfred.models import HomeContract, Member
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.panel_api.today_local", lambda: TODAY)


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        return str(m.dashboard_token)


def _c(lab: Lab, kind: str, provider: str, amount, days: int, status: str = "active"):
    return HomeContract(
        member_id=lab.member_id,
        kind=kind,
        provider=provider,
        amount=amount,
        ends_on=TODAY + timedelta(days=days),
        status=status,
    )


@db
async def test_without_contracts_the_card_is_empty_with_a_hint(lab: Lab, client, today) -> None:
    tok = await _token(lab.member_id)
    body = (await client.get(f"/api/d/{tok}/home")).json()
    assert [c["id"] for c in body["cards"]] == ["home_contracts"]
    card = body["cards"][0]
    assert card["empty"] is True and "contrato energia" in card["hint"]["text"]


@db
async def test_contracts_are_listed_soonest_first_with_the_monthly_cost(
    lab: Lab, client, today
) -> None:
    await lab.add(
        _c(lab, "energy", "Vattenfall", 120, 400),
        _c(lab, "internet", "Ziggo", 55.5, 83),
        _c(lab, "gas", "Essent", None, 200),
        _c(lab, "gas", "Draft", 999, 10, status="draft"),
    )
    tok = await _token(lab.member_id)
    body = (await client.get(f"/api/d/{tok}/home")).json()
    assert [c["id"] for c in body["cards"]] == ["home_contracts", "home_cost"]
    items = body["cards"][0]["items"]
    assert [i["provider"] for i in items] == ["Ziggo", "Essent", "Vattenfall"]
    assert items[0]["days"] == 83 and items[0]["label"] == "Internet"
    cost = body["cards"][1]
    assert cost["values"] == {"monthly": 175.5, "yearly": 2106.0}
    assert {i["provider"] for i in cost["items"]} == {"Vattenfall", "Ziggo"}
    assert "Draft" not in str(body)


@db
async def test_only_the_owner_sees_the_contracts(lab: Lab, lab2: Lab, client, today) -> None:
    await lab.add(_c(lab, "energy", "SEGREDO", 100, 100))
    tok2 = await _token(lab2.member_id)
    body = (await client.get(f"/api/d/{tok2}/home")).json()
    assert "SEGREDO" not in str(body) and body["cards"][0]["empty"] is True
