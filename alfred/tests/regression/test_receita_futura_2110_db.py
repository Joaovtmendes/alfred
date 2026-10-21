# ruff: noqa: E501
"""B-072: "vou receber 300 em dois dias" is an entry to receive on its day, not money received today."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import select

from alfred.clock import today_local
from alfred.models import Expense
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _item(amount: float, name: str, type_: str, pending=None) -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": "inkomen" if type_ == "income" else "overig",
            "description": None, "type": type_, "days_ago": 0, "pending_in_days": pending}  # fmt: skip


async def _rows(lab: Lab) -> list[Expense]:
    rows = await lab.rows(select(Expense).where(Expense.member_id == lab.member_id))
    return [r[0] for r in rows]


@db
@pytest.mark.bug("B-072")
async def test_a_future_income_in_a_list_is_saved_to_receive_on_its_day(lab: Lab) -> None:
    lab.multi.return_value = [
        _item(300, "Freelancer", "income", 2),
        _item(5000, "Salário", "income"),
    ]
    out = await lab.say(
        "vou receber 300 do freelancer em dois dias, recebi outro salario hoje de 5000"
    )
    due = today_local() + timedelta(days=2)
    assert f"a receber em {due:%d/%m}" in out
    await lab.tap(lab.buttons[-1][0][0])  # Confirmar
    by = {r.merchant: r for r in await _rows(lab)}
    assert by["Salário"].status == "received"
    assert by["Freelancer"].status == "to_receive"
    assert by["Freelancer"].expense_date.date() == due
    bal = await lab.say("saldo")
    assert "5.000,00" in bal and "Previsto" in bal and "300,00 a receber" in bal


@db
@pytest.mark.bug("B-072")
async def test_adjusting_a_draft_item_to_a_later_day(lab: Lab) -> None:
    lab.multi.return_value = [_item(300, "Freelancer", "income"), _item(5000, "Salário", "income")]
    await lab.say("recebi 300 do freelancer e salario 5000")
    out = await lab.say("ajustar freelancer para daqui 2 dias")
    due = today_local() + timedelta(days=2)
    assert f"a receber em {due:%d/%m}" in out
    await lab.tap(lab.buttons[-1][0][0])
    by = {r.merchant: r for r in await _rows(lab)}
    assert by["Freelancer"].status == "to_receive"


@db
@pytest.mark.bug("B-072")
async def test_a_single_future_income_is_saved_pending(lab: Lab) -> None:
    lab.expense.return_value = {"amount": 300.0, "currency": "EUR", "merchant": "Freelancer", "category": "inkomen",
                                "description": "freelance", "type": "income", "days_ago": 0, "pending_in_days": 2}  # fmt: skip
    out = await lab.say("vou receber 300 do freelancer em dois dias")
    assert "Anotei a receber" in out
    (row,) = await _rows(lab)
    assert row.status == "to_receive" and row.expense_date.date() == today_local() + timedelta(
        days=2
    )
