"""M4c — Query routing tests.

Covers:
  - saldo command keyword routing
  - classify_query dispatch: balance, category, period, comparison
  - _period_range helper
  - _build_summary with period/category params
  - _build_saldo / _build_comparison
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alfred.conversation import (
    _build_comparison,
    _build_saldo,
    _build_summary,
    _period_range,
    handle_inbound,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_member(consent_state: str = "accepted") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        household_id=uuid.uuid4(),
        wa_phone="31600000001",
        consent_state=consent_state,
        display_name="Test",
        disclosure_accepted_at=None,
        disclosure_version=None,
    )


def _make_message(body: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        body=body,
        direction="inbound",
        wa_message_id="wamid.test",
        wa_timestamp=datetime.now(timezone.utc),
    )


def _mock_session(records=None) -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    execute_result = MagicMock()
    execute_result.scalars.return_value.all.return_value = records or []
    session.execute = AsyncMock(return_value=execute_result)
    return session


def _make_expense(
    amount: float,
    txn_type: str = "expense",
    category: str = "supermarkt",
    merchant: str | None = "Jumbo",
    description: str = "supermarkt",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        amount=amount,
        transaction_type=txn_type,
        category=category,
        merchant=merchant,
        description=description,
        expense_date=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# _period_range unit tests
# ---------------------------------------------------------------------------

def test_period_range_current_month():
    start, end, label = _period_range("current_month")
    assert start.day == 1
    assert end is None
    assert label  # non-empty label


def test_period_range_last_month():
    start, end, label = _period_range("last_month")
    assert end is not None
    assert end.day == 1  # first of current month
    assert start.day == 1  # first of previous month
    assert start < end


def test_period_range_current_week():
    start, end, label = _period_range("current_week")
    assert start.weekday() == 0  # Monday
    assert end is None
    assert label == "esta semana"


def test_period_range_last_week():
    start, end, label = _period_range("last_week")
    assert end is not None
    assert end.weekday() == 0  # Monday (start of current week)
    assert start.weekday() == 0  # Monday (start of previous week)
    assert (end - start).days == 7
    assert label == "semana passada"


# ---------------------------------------------------------------------------
# _build_summary with category / period
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_summary_empty_returns_message():
    member = _make_member()
    session = _mock_session(records=[])
    result = await _build_summary(member, session)
    assert "Sem registos" in result


@pytest.mark.asyncio
async def test_build_summary_with_category_filters():
    member = _make_member()
    expense = _make_expense(45.0, category="supermarkt", merchant="Jumbo")
    session = _mock_session(records=[expense])
    result = await _build_summary(member, session, category="supermarkt")
    assert "Supermarkt" in result or "supermarkt" in result.lower()
    assert "45" in result


@pytest.mark.asyncio
async def test_build_summary_last_month():
    member = _make_member()
    session = _mock_session(records=[])
    result = await _build_summary(member, session, period="last_month")
    assert "Sem registos" in result


@pytest.mark.asyncio
async def test_build_summary_with_income_shows_saldo():
    member = _make_member()
    records = [
        _make_expense(100.0, txn_type="expense"),
        _make_expense(500.0, txn_type="income", category="inkomen", merchant=None),
    ]
    session = _mock_session(records=records)
    result = await _build_summary(member, session)
    assert "Saldo" in result
    assert "Receitas" in result


# ---------------------------------------------------------------------------
# _build_saldo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_saldo_empty():
    member = _make_member()
    session = _mock_session(records=[])
    result = await _build_saldo(member, session)
    assert "Sem registos" in result


@pytest.mark.asyncio
async def test_build_saldo_positive_balance():
    member = _make_member()
    records = [
        _make_expense(2800.0, txn_type="income", category="inkomen", merchant=None),
        _make_expense(300.0, txn_type="expense"),
    ]
    session = _mock_session(records=records)
    result = await _build_saldo(member, session)
    assert "Saldo" in result
    assert "2.800" in result or "2800" in result  # income shown


@pytest.mark.asyncio
async def test_build_saldo_negative_balance():
    member = _make_member()
    records = [
        _make_expense(500.0, txn_type="expense"),
    ]
    session = _mock_session(records=records)
    result = await _build_saldo(member, session)
    assert "-" in result  # negative balance shown


# ---------------------------------------------------------------------------
# _build_comparison
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_comparison_no_data():
    member = _make_member()
    session = _mock_session(records=[])
    result = await _build_comparison(member, session)
    assert "Comparação" in result


# ---------------------------------------------------------------------------
# handle_inbound — saldo command routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("word", ["saldo", "Saldo", "SALDO", "balanço"])
@pytest.mark.asyncio
async def test_saldo_command_routing(word: str) -> None:
    """saldo keyword → _build_saldo called, not LLM."""
    member = _make_member("accepted")
    message = _make_message(word)
    session = _mock_session(records=[])

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as mock_llm,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=None),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
    mock_llm.assert_not_awaited()


# ---------------------------------------------------------------------------
# handle_inbound — classify_query dispatch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_query_category_routing() -> None:
    """classify_query returns category query → _build_summary called, no LLM."""
    member = _make_member("accepted")
    message = _make_message("quanto gastei em supermercado?")
    session = _mock_session(records=[])

    query_result = {"query_type": "category", "category": "supermarkt", "period": "current_month"}

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as mock_llm,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=query_result),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_period_routing() -> None:
    """classify_query returns period query → _build_summary called, no LLM."""
    member = _make_member("accepted")
    message = _make_message("gastos desta semana")
    session = _mock_session(records=[])

    query_result = {"query_type": "period", "category": None, "period": "current_week"}

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as mock_llm,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=query_result),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_comparison_routing() -> None:
    """classify_query returns comparison → _build_comparison called, no LLM."""
    member = _make_member("accepted")
    message = _make_message("compara este mês com o mês passado")
    session = _mock_session(records=[])

    query_result = {"query_type": "comparison", "category": None, "period": None}

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as mock_llm,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=query_result),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_balance_routing() -> None:
    """classify_query returns balance → _build_saldo called, no LLM."""
    member = _make_member("accepted")
    message = _make_message("qual é o meu saldo?")
    session = _mock_session(records=[])

    query_result = {"query_type": "balance", "category": None, "period": "current_month"}

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock) as mock_llm,
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=query_result),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
    mock_llm.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_query_falls_through_to_llm() -> None:
    """classify_query returns None → falls through to generate_reply."""
    member = _make_member("accepted")
    message = _make_message("qual é o tempo hoje?")
    session = _mock_session(records=[])

    with (
        patch("alfred.conversation.send_text", new_callable=AsyncMock) as mock_send,
        patch("alfred.conversation.generate_reply", new_callable=AsyncMock, return_value="Está sol!"),
        patch("alfred.conversation.extract_expense", new_callable=AsyncMock, return_value=None),
        patch("alfred.conversation.classify_query", new_callable=AsyncMock, return_value=None),
    ):
        await handle_inbound(member, message, session)

    mock_send.assert_awaited_once()
