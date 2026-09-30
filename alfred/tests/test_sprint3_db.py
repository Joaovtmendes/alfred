"""Sprint 3: no phantom confirmations, euro-only, several items per message, safer edits.

Real PostgreSQL (ALFRED_TEST_DB=1); see labkit.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local
from alfred.conversation import (
    _FAKE_CONFIRM_RE,
    _claims_recorded,
    _has_zero_or_negative_amount,
    _looks_multi,
)
from alfred.models import Expense
from tests.labkit import Lab


def test_claims_recorded_detects_false_confirmations() -> None:
    assert _claims_recorded("Pizza — €12,00 registada.")
    assert _claims_recorded("Farmácia foi registado: 10,00.")
    assert _claims_recorded("Saved your expense")
    assert not _claims_recorded("Envia cada despesa separada: Mercado 25")


def test_looks_multi_needs_two_amounts() -> None:
    assert _looks_multi("fui no mercado e gastei 20,00 e 10 de farmácia")
    assert _looks_multi("Mercado 20\nFarmácia 10")
    assert not _looks_multi("Jumbo 23,50")
    assert not _looks_multi("olá")


pytestmark_db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _item(amount, name, *, category="supermarkt", currency="EUR", kind="expense"):
    return {
        "amount": amount,
        "currency": currency,
        "merchant": name,
        "category": category,
        "description": name,
        "type": kind,
        "days_ago": 0,
    }


async def _count(lab: Lab) -> int:
    return await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )


@pytestmark_db
async def test_llm_false_record_claim_is_replaced(lab: Lab) -> None:
    lab.llm_reply.return_value = "Pizza — €12,00 registada. Renda — €99.999,00 registada."
    reply = await lab.say("vou ao cinema e depois janto fora")
    assert "registada" not in reply.lower().replace("não registei", "")
    assert (
        "Mercado 20" in reply and "Anotei" not in reply
    )  # no number → neutral, not the expense hint
    assert await _count(lab) == 0


@pytestmark_db
async def test_multi_expense_message_records_every_item(lab: Lab) -> None:
    lab.multi.return_value = [
        _item(20, "Mercado"),
        _item(10, "Farmácia", category="gezondheid"),
    ]
    reply = await lab.say("Fui no mercado e gastei 20,00 e 10 de farmácia")
    assert "(2)" in reply
    assert "Mercado" in reply and "Farmácia" in reply
    assert await _count(lab) == 2
    lab.llm_reply.assert_not_awaited()


@pytestmark_db
async def test_multi_skips_foreign_currency_but_records_euro_items(lab: Lab) -> None:
    lab.multi.return_value = [_item(20, "Mercado"), _item(50, "Hotel", currency="USD")]
    reply = await lab.say("Mercado 20 e hotel 50 dollars")
    assert await _count(lab) == 1
    assert "USD" in reply and "euros" in reply


@pytestmark_db
async def test_single_foreign_currency_is_not_stored(lab: Lab) -> None:
    lab.expense.return_value = _item(50, "Amazon", currency="USD")
    reply = await lab.say("Amazon 50 dollars")
    assert "só trabalho em euros" in reply
    assert await _count(lab) == 0


@pytestmark_db
async def test_high_value_gets_a_hint_and_small_does_not(lab: Lab) -> None:
    lab.expense.return_value = _item(1200, "Computador", category="overig")
    big = await lab.say("computador 1200")
    assert "valor alto" in big
    assert "confere se está certo" in big
    lab.expense.return_value = _item(23, "Jumbo")
    assert "valor alto" not in await lab.say("jumbo 23")


@pytestmark_db
async def test_ultimas_despesas_excludes_income(lab: Lab) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="income",
            amount=3000,
            merchant="Salário",
            category="inkomen",
            expense_date=now_local(),
        ),
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=25,
            merchant="Mercado",
            category="supermarkt",
            expense_date=now_local() - timedelta(days=1),
        ),
    )
    reply = await lab.say("ultimas 5 despesas")
    assert "Mercado" in reply and "Salário" not in reply


@pytestmark_db
async def test_correction_ignores_expenses_older_than_the_window(lab: Lab) -> None:
    old = Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type="expense",
        amount=1200,
        merchant="Computador",
        category="overig",
        expense_date=now_local(),
    )
    await lab.add(old)
    async with __import__("alfred.db", fromlist=["AsyncSessionLocal"]).AsyncSessionLocal() as s:
        from sqlalchemy import update

        await s.execute(
            update(Expense)
            .where(Expense.id == old.id)
            .values(created_at=now_local() - timedelta(hours=5))
        )
        await s.commit()
    reply = await lab.say("errei foram 99")
    assert "Corrigido" not in reply
    amount = await lab.scalar(select(Expense.amount).where(Expense.id == old.id))
    assert float(amount) == 1200.0


def test_fake_confirmation_and_zero_negative_detectors() -> None:
    assert _FAKE_CONFIRM_RE.search(
        'Café 0,00 - aguarda confirmação. Confirma cada um com "sim" ou "não"'
    )
    assert _FAKE_CONFIRM_RE.search("Please reply yes / no to confirm")
    assert not _FAKE_CONFIRM_RE.search("Envia cada despesa separada: Mercado 25")
    assert _has_zero_or_negative_amount("Café 0 e reembolso -15")
    assert _has_zero_or_negative_amount("café 0,00")
    assert not _has_zero_or_negative_amount("Mercado 20 e farmácia 10,50")
    assert not _has_zero_or_negative_amount("renda 100 e gás 205")


@pytestmark_db
async def test_llm_invented_confirmation_is_replaced_with_real_question(lab: Lab) -> None:
    lab.multi.return_value = []
    lab.expense.return_value = None
    lab.llm_reply.return_value = (
        "Recebi 2 registos:\n• Café 0,00 - aguarda confirmação\n"
        'Confirma cada um com "sim" ou "não".'
    )
    reply = await lab.say("Café 0 e reembolso -15")
    assert "aguarda confirmação" not in reply
    assert "zero ou negativo" in reply
    assert await _count(lab) == 0


def test_fake_question_patterns_and_bare_zero_negative() -> None:
    assert _FAKE_CONFIRM_RE.search("Supermercado 23\nMercado 25\n\nÉ isto correto?")
    assert _FAKE_CONFIRM_RE.search("Ginásio — quanto?")
    assert not _FAKE_CONFIRM_RE.search("Ginásio — €10,00 registado")
    assert _has_zero_or_negative_amount("Café 0")
    assert _has_zero_or_negative_amount("reembolso -15")
    assert not _has_zero_or_negative_amount("hotel 120 - 3 noites")
    assert not _has_zero_or_negative_amount("almoço 20 de 2026-05-10")


@pytestmark_db
async def test_zero_and_negative_amounts_get_deterministic_reply_without_llm(lab: Lab) -> None:
    lab.multi.return_value = []
    lab.expense.return_value = None
    reply = await lab.say("Café 0 e reembolso -15")
    assert "zero ou negativo" in reply
    assert await _count(lab) == 0
    lab.llm_reply.assert_not_awaited()


# ── Reply buttons (Sprint 5): Undo / Edit / It's right, no pending state ────────


async def _one_expense(lab, amount=12.5, name="Jumbo"):
    lab.expense.return_value = {
        "amount": amount,
        "currency": "EUR",
        "merchant": name,
        "category": "supermercado",
        "description": name,
        "type": "expense",
        "days_ago": 0,
    }
    reply = await lab.say(f"{name} {amount}")
    exp = await lab.scalar(select(Expense).where(Expense.member_id == lab.member_id))
    return reply, exp


async def test_recorded_expense_offers_edit_and_undo_buttons(lab):
    _, exp = await _one_expense(lab)
    assert lab.buttons[-1] == [(f"edit:{exp.id}", "Editar"), (f"undo:{exp.id}", "Desfazer")]


async def test_high_value_offers_its_right_and_undo(lab):
    _, exp = await _one_expense(lab, amount=2500)
    assert lab.buttons[-1] == [(f"ok:{exp.id}", "Está certo"), (f"undo:{exp.id}", "Desfazer")]


async def test_undo_button_deletes_only_that_expense(lab):
    _, exp = await _one_expense(lab)
    reply = await lab.tap(f"undo:{exp.id}")
    assert "Apaguei" in reply or "apaguei" in reply
    assert await lab.scalar(select(Expense).where(Expense.id == exp.id)) is None


async def test_undo_button_twice_says_gone(lab):
    _, exp = await _one_expense(lab)
    await lab.tap(f"undo:{exp.id}")
    assert "já tinha sido apagado" in await lab.tap(f"undo:{exp.id}")


async def test_ok_and_edit_buttons_keep_the_expense(lab):
    _, exp = await _one_expense(lab, amount=2500)
    assert "fica assim" in await lab.tap(f"ok:{exp.id}")
    assert "valor certo" in await lab.tap(f"edit:{exp.id}")
    assert await lab.scalar(select(Expense).where(Expense.id == exp.id)) is not None


async def test_button_with_unknown_expense_id_deletes_nothing(lab):
    import uuid as _uuid

    reply = await lab.tap(f"undo:{_uuid.uuid4()}")
    assert "já tinha sido apagado" in reply
