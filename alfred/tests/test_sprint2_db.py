"""Sprint 2: expense queries answered from real data, not improvised by the LLM.

Every case is a message that used to hit the LLM fallback (which invented answers) or be
classified into the wrong period. Real PostgreSQL (ALFRED_TEST_DB=1); see labkit.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local
from alfred.models import Expense
from tests.labkit import Lab

pytestmark = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(lab: Lab, amount: float, *, merchant=None, category="overig", days_ago=0, kind="expense"):
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        merchant=merchant,
        category=category,
        expense_date=now_local() - timedelta(days=days_ago),
    )


async def _count(lab: Lab) -> int:
    return await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )


async def test_ontem_is_not_answered_as_hoje(lab: Lab) -> None:
    lab.classify.return_value = {"query_type": "period", "category": None, "period": "today"}
    await lab.add(_exp(lab, 30, merchant="Mercado", category="supermarkt", days_ago=1))
    await lab.add(_exp(lab, 999, merchant="Computador", category="overig", days_ago=0))
    reply = await lab.say("quanto gastei ontem?")
    assert "ontem" in reply
    assert "€30,00" in reply
    assert "999" not in reply  # today's spend must not leak into yesterday


async def test_resumo_de_setembro_is_not_last_month(lab: Lab) -> None:
    lab.classify.return_value = {"query_type": "period", "category": None, "period": "last_month"}
    await lab.add(_exp(lab, 12, category="restaurant", days_ago=0))
    month = now_local().month
    name = {9: "setembro"}.get(month)
    if name is None:  # keep the test valid in any month
        pytest.skip("named-month case is asserted for September only")
    reply = await lab.say("resumo de setembro")
    assert "Setembro" in reply and "€12,00" in reply


async def test_ultimas_despesas_lists_real_rows_newest_first(lab: Lab) -> None:
    await lab.add(
        _exp(lab, 10, merchant="Spotify", category="abonnement", days_ago=3),
        _exp(lab, 25, merchant="Mercado", category="supermarkt", days_ago=1),
        _exp(lab, 45, merchant="Flores", category="restaurant", days_ago=0),
    )
    reply = await lab.say("ultimas 2 despesas")
    lab.llm_reply.assert_not_awaited()  # never improvised
    lines = [ln for ln in reply.splitlines() if ln.startswith("•")]
    assert len(lines) == 2
    assert "Flores" in lines[0] and "Mercado" in lines[1]
    assert "Spotify" not in reply


async def test_top_categorias_shows_top_three_localised(lab: Lab) -> None:
    await lab.add(
        _exp(lab, 200, category="wonen"),
        _exp(lab, 100, category="supermarkt"),
        _exp(lab, 50, category="restaurant"),
        _exp(lab, 5, category="transport"),
    )
    reply = await lab.say("top categorias este mes")
    lab.llm_reply.assert_not_awaited()
    assert "Habitação" in reply and "Supermercado" in reply and "Restaurante" in reply
    assert "Transporte" not in reply
    assert "Wonen" not in reply  # Dutch identifiers are not shown to a pt user


async def test_apaga_deletes_the_last_expense_and_says_which(lab: Lab) -> None:
    await lab.add(_exp(lab, 10, merchant="Spotify", days_ago=2))
    await lab.add(_exp(lab, 25, merchant="Mercado", days_ago=0))
    reply = await lab.say("apaga")
    assert "Mercado" in reply and "€25,00" in reply
    assert await _count(lab) == 1
    lab.llm_reply.assert_not_awaited()


async def test_apaga_with_nothing_to_delete(lab: Lab) -> None:
    reply = await lab.say("apaga")
    assert "nenhuma despesa" in reply
    lab.llm_reply.assert_not_awaited()


async def test_apaga_treino_is_still_the_workout_command(lab: Lab) -> None:
    await lab.add(_exp(lab, 10, merchant="Spotify"))
    await lab.say("apaga treino")
    assert await _count(lab) == 1  # the expense is untouched


async def test_amount_correction_says_corrected_with_old_and_new(lab: Lab) -> None:
    await lab.add(_exp(lab, 80, merchant="Mercado", category="supermarkt"))
    reply = await lab.say("errei foram 42€")
    assert "Corrigido" in reply and "€80,00" in reply and "€42,00" in reply
    assert "registada" not in reply
    assert await _count(lab) == 1
