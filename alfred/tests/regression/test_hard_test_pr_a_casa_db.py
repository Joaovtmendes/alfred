"""B-094..B-097: contracts ask what is missing and become a fixed bill; services read prices."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from alfred import legal
from alfred.db import AsyncSessionLocal
from alfred.models import HomeContract, RecurringItem, ServiceRecord
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _all(model):
    async with AsyncSessionLocal() as s:
        return list((await s.scalars(select(model))).all())


@db
@pytest.mark.bug("B-094")
async def test_contract_without_provider_asks_for_it(lab: Lab) -> None:
    ask = await lab.say("contrato energia 120 até 31/12/2027")
    assert "fornecedor" in ask and not await _all(HomeContract)
    shown = await lab.say("EDP")
    assert "120,00" in shown and lab.buttons[-1]


@db
@pytest.mark.bug("B-095")
async def test_confirmed_contract_becomes_fixed_bill_with_end(lab: Lab) -> None:
    await lab.say("contrato energia 120 até 31/12/2027")
    await lab.say("EDP")
    ask_day = await lab.tap(lab.buttons[-1][0][0])
    assert "dia do mês" in ask_day
    paid_q = await lab.say("dia 10")
    assert "conta fixa no dia 10" in paid_q and len(lab.buttons[-1]) == 2
    await lab.tap(lab.buttons[-1][0][0])
    (item,) = await _all(RecurringItem)
    assert item.kind == "fixed" and item.due_day == 10
    assert item.end_date is not None and item.end_date.year == 2027


@db
@pytest.mark.bug("B-096")
async def test_service_price_without_euro_and_edits(lab: Lab) -> None:
    draft = await lab.say("guarda serviço encanador Pedro 180")
    assert "€180,00" in draft and "Faltam" in draft
    draft = await lab.say("garantia 12 meses")
    assert "12 meses" in draft and "Faltam" not in draft
    draft = await lab.say("valor 200")
    assert "€200,00" in draft
    assert len(await _all(ServiceRecord)) == 1  # one draft, edited


@db
@pytest.mark.bug("B-096")
async def test_bare_service_amount_offers_expense_button(lab: Lab) -> None:
    draft = await lab.say("serviço: 180 €")
    assert "€180,00" in draft
    assert any(b[0].startswith("svc_exp:") for b in lab.buttons[-1])


@pytest.mark.bug("B-097")
def test_policy_says_it_does_not_replace_invoice() -> None:
    for lang in ("pt", "en", "nl"):
        html = legal.render_policy(lang).lower()
        assert ("nota fiscal" in html) or ("invoice" in html) or ("factuur" in html)
