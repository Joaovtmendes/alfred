"""B-076: "saldo" answers only the balance; the detail and a month-end estimate are one tap away."""

from __future__ import annotations

import os
from datetime import date

import pytest

from alfred import balance_view
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str) -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": "supermarkt",
            "description": None, "type": "expense", "days_ago": 0}  # fmt: skip


@pytest.mark.bug("B-076")
def test_the_estimate_needs_a_week_of_the_month_and_follows_the_pace() -> None:
    assert balance_view.projection(100, date(2026, 10, 6)) is None
    assert balance_view.projection(0, date(2026, 10, 20)) is None
    assert balance_view.projection(310, date(2026, 10, 10)) == 961.0  # 31 / 10 x 310


@db
@pytest.mark.bug("B-076")
async def test_saldo_is_short_and_the_detail_button_gives_the_rest(lab: Lab) -> None:
    lab.expense.return_value = _exp(20, "Jumbo")
    await lab.say("Jumbo 20")
    short = await lab.say("saldo")
    assert "20,00" in short and "Receitas" not in short and "Despesas" not in short
    assert lab.buttons[-1][0][0].startswith("bal_detail:")
    detail = await lab.tap(lab.buttons[-1][0][0])
    assert "Receitas" in detail and "Despesas" in detail and "não o saldo do banco" in detail


@db
@pytest.mark.bug("B-076")
async def test_a_named_month_keeps_the_full_answer(lab: Lab) -> None:
    lab.expense.return_value = _exp(20, "Jumbo")
    await lab.say("Jumbo 20")
    out = await lab.say("saldo de setembro")
    assert "setembro" in out.lower() or "Sem lançamentos" in out or "Nenhum" in out
