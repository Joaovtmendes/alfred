# ruff: noqa: E501
"""Serious defects found by reading the code (07/10), fixed before the manual hard test."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from alfred.conversation import _t
from alfred.db import AsyncSessionLocal
from alfred.ledger_status import NewPending, parse_pending
from alfred.models import Expense, HabitLog, HealthLog, Member, PendingBatch, RecurringItem
from alfred.recurring import parse_recurring
from alfred.validation import sanitize_expense
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(
    amount: float,
    name: str,
    category: str = "supermarkt",
    kind: str = "expense",
    currency: str = "EUR",
) -> dict:
    return {"amount": amount, "currency": currency, "merchant": name, "category": category,
            "description": name, "type": kind, "days_ago": 0}  # fmt: skip


async def _count(lab: Lab, model) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id)
    )


async def _add_exp(
    lab: Lab, amount: float, name: str, minutes_ago: int = 0, kind: str = "expense"
) -> uuid.UUID:
    eid = uuid.uuid4()
    await lab.add(Expense(id=eid, member_id=lab.member_id, household_id=lab.household_id, transaction_type=kind,
                          amount=amount, currency="EUR", merchant=name, description=name,
                          category="inkomen" if kind == "income" else "supermarkt",
                          expense_date=datetime.now(UTC) - timedelta(minutes=minutes_ago),
                          created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago)))  # fmt: skip
    return eid


# ── 1. habit fast path must not swallow expenses ──────────────────────────────


@db
@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("gastei 12 no tivoli", "Tivoli"),
        ("comprei ravioli 12", "Ravioli"),
        ("comprei pão ali 5", "Padaria"),
        ("uber zona leste 15", "Uber"),
    ],
)
async def test_words_ending_in_li_are_expenses_not_habits(lab: Lab, text, name) -> None:
    lab.expense.return_value = _exp(12, name)
    out = await lab.say(text)
    assert name in out
    assert await _count(lab, HabitLog) == 0 and await _count(lab, Expense) == 1


@db
@pytest.mark.parametrize("text", ["li 30 páginas", "meditei hoje", "hoje meditei", "eu estudei"])
async def test_real_habit_logs_still_work(lab: Lab, text) -> None:
    await lab.say(text)
    assert await _count(lab, HabitLog) == 1


@db
async def test_trip_name_with_li_is_not_a_habit(lab: Lab) -> None:
    await lab.say("criar viagem Bali")
    assert await _count(lab, HabitLog) == 0


# ── 13. command words at the start must not eat an expense ────────────────────


@db
async def test_help_and_balance_words_with_an_amount_are_expenses(lab: Lab) -> None:
    lab.expense.return_value = _exp(50, "Mudança", "overig")
    out = await lab.say("ajuda com a mudança 50")
    assert out != _t("help", "pt") and await _count(lab, Expense) == 1
    lab.expense.return_value = _exp(300, "Conta", "overig")
    out = await lab.say("saldo da conta 300")
    assert not out.startswith("*Saldo") and await _count(lab, Expense) == 2


@db
@pytest.mark.parametrize("text", ["ajuda", "me ajuda", "menu", "o que você faz?"])
async def test_help_phrases(lab: Lab, text) -> None:
    assert await lab.say(text) == _t("help", "pt")


@db
async def test_balance_still_works(lab: Lab) -> None:
    await _add_exp(lab, 20, "Mercado")
    assert (await lab.say("saldo")).startswith("*Saldo")


@db
@pytest.mark.parametrize("text", ["painel", "meu painel", "abrir painel"])
async def test_panel_words_open_the_dashboard(lab: Lab, text) -> None:
    out = await lab.say(text)
    assert out != "[llm]"
    lab.llm_reply.assert_not_awaited()


# ── 4. "apaga" asks first ─────────────────────────────────────────────────────


@db
async def test_delete_last_asks_before_deleting(lab: Lab) -> None:
    eid = await _add_exp(lab, 2800, "Salário", kind="income")
    out = await lab.say("apaga")
    assert "Salário" in out and "€2.800,00" in out
    assert [b[0] for b in lab.buttons[-1]] == [f"undo:{eid}", f"ok:{eid}"]
    assert await _count(lab, Expense) == 1
    await lab.tap(f"ok:{eid}")
    assert await _count(lab, Expense) == 1
    await lab.say("apaga")
    await lab.tap(f"undo:{eid}")
    assert await _count(lab, Expense) == 0


# ── 5. foreign currencies are refused, not stored as euros ────────────────────


def test_sanitizer_keeps_an_unknown_currency_code() -> None:
    assert sanitize_expense(_exp(50, "x", currency="BRL"))["currency"] == "BRL"
    assert sanitize_expense(_exp(50, "x", currency="euro?"))["currency"] == "EUR"


@db
@pytest.mark.parametrize(
    "text",
    [
        "gastei 50 reais no mercado",
        "paguei R$ 80 no uber",
        "gastei 20 dólares na farmácia",
        "lunch 15 dollars",
    ],
)
async def test_text_in_another_currency_is_refused(lab: Lab, text) -> None:
    lab.expense.return_value = _exp(50, "Mercado")  # the model said EUR
    out = await lab.say(text)
    assert "euro" in out.lower()
    assert await _count(lab, Expense) == 0


@db
async def test_euro_text_is_recorded(lab: Lab) -> None:
    lab.expense.return_value = _exp(50, "Mercado")
    await lab.say("gastei 50 no mercado")
    assert await _count(lab, Expense) == 1


# ── 10. [Editar] corrects the entry that was tapped ───────────────────────────


@db
async def test_edit_button_targets_its_own_entry(lab: Lab) -> None:
    padaria = await _add_exp(lab, 6, "Padaria", minutes_ago=5)
    mercado = await _add_exp(lab, 20, "Mercado", minutes_ago=1)
    await lab.tap(f"edit:{padaria}")
    out = await lab.say("na verdade foi 8")
    assert "Padaria" in out and "€8,00" in out
    async with AsyncSessionLocal() as s:
        assert float((await s.get(Expense, padaria)).amount) == 8.0
        assert float((await s.get(Expense, mercado)).amount) == 20.0


@db
async def test_edit_button_accepts_a_bare_amount(lab: Lab) -> None:
    padaria = await _add_exp(lab, 6, "Padaria", minutes_ago=300)  # older than the 2 h window
    await lab.tap(f"edit:{padaria}")
    await lab.say("7,50")
    async with AsyncSessionLocal() as s:
        assert float((await s.get(Expense, padaria)).amount) == 7.5


@db
async def test_correction_with_thousands_separator(lab: Lab) -> None:
    eid = await _add_exp(lab, 1150, "Aluguel")
    await lab.say("errei foram 1.200")
    async with AsyncSessionLocal() as s:
        assert float((await s.get(Expense, eid)).amount) == 1200.0


# ── 11. "todo mês" does not turn spending into a fixed bill ───────────────────


@pytest.mark.parametrize(
    "text",
    [
        "gastei 50 no mercado todo mês",
        "salário 3000 todo dia 25",
        "paguei 30 de academia todo mês",
        "recebi 200 por mês",
    ],
)
def test_spending_or_income_is_not_a_fixed_bill(text) -> None:
    assert parse_recurring(text, text) is None


def test_real_fixed_bill_still_parses() -> None:
    assert parse_recurring("aluguel 1200 todo dia 1", "aluguel 1200 todo dia 1") is not None


@db
async def test_same_fixed_bill_twice_is_not_duplicated(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    out = await lab.say("aluguel 1200 todo dia 1")
    assert "Aluguel" in out and await _count(lab, RecurringItem) == 1


# ── 12. an insurance expense is not a deadline question ───────────────────────


@db
async def test_insurance_expense_with_date_is_recorded(lab: Lab) -> None:
    lab.expense.return_value = _exp(130, "Seguro saúde", "verzekering")
    await lab.say("paguei 130 de seguro saúde, data 05/10")
    assert await _count(lab, Expense) == 1


@db
async def test_insurance_deadline_question_still_answers(lab: Lab) -> None:
    assert "31/12" in await lab.say("quando posso trocar de seguro saúde?")


# ── 2 & 3. batch draft answers are whole messages; more than 10 items is said ─


async def _draft(lab: Lab, n: int = 2) -> None:
    lab.multi.return_value = [_exp(10 + i, f"loja{i}") for i in range(n)]
    await lab.say("loja0 10 e loja1 11")


@db
@pytest.mark.parametrize(
    "text", ["almoço no shopping", "já paguei", "isso é transporte", "pode ser amanhã"]
)
async def test_sentences_do_not_answer_an_open_draft(lab: Lab, text) -> None:
    await _draft(lab)
    lab.expense.return_value = None
    await lab.say(text)
    assert await _count(lab, PendingBatch) == 1
    assert await _count(lab, Expense) == 0


@db
@pytest.mark.parametrize(
    ("text", "saved"),
    [("sim", 2), ("pode confirmar", 2), ("ok, pode gravar", 2), ("não", 0), ("cancela tudo", 0)],
)
async def test_clear_answers_still_work(lab: Lab, text, saved) -> None:
    await _draft(lab)
    await lab.say(text)
    assert await _count(lab, PendingBatch) == 0 and await _count(lab, Expense) == saved


@db
async def test_more_than_ten_items_are_announced(lab: Lab) -> None:
    lab.multi.return_value = [_exp(1 + i, f"loja{i}") for i in range(12)]
    out = await lab.say(", ".join(f"loja{i} {1 + i}" for i in range(12)))
    assert "10" in out and "outra mensagem" in out


# ── 9. pending entries: marker in the middle, extra words when settling ───────


def test_pending_marker_in_the_middle() -> None:
    got = parse_pending("luz 80 a pagar dia 5", "luz 80 a pagar dia 5", date(2026, 10, 8))
    assert isinstance(got, NewPending) and got.name.lower() == "luz" and got.amount == 80.0
    assert parse_pending("quanto falta a pagar", "quanto falta a pagar", date(2026, 10, 8)) is None


@db
async def test_settle_with_extra_words(lab: Lab) -> None:
    await lab.say("a pagar luz 120 dia 20")
    await lab.say("já paguei a conta de luz")
    rows = await lab.rows(select(Expense.status).where(Expense.member_id == lab.member_id))
    assert [r[0] for r in rows] == ["paid"]


# ── 6 & 7. health data needs explicit consent; no note suggestion ─────────────


async def _no_consent(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Member).where(Member.id == lab.member_id).values(health_consent_at=None)
        )
        await s.commit()


@db
async def test_health_log_asks_for_consent_first(lab: Lab) -> None:
    await _no_consent(lab)
    lab.health.return_value = {
        "log_type": "sleep",
        "value": "7",
        "unit": "h",
        "notes": None,
        "days_ago": 0,
    }
    out = await lab.say("dormi 7h")
    assert "saúde" in out and [b[0] for b in lab.buttons[-1]] == ["hc_yes:0", "hc_no:0"]
    lab.health.assert_not_awaited()
    assert await _count(lab, HealthLog) == 0
    await lab.tap("hc_no:0")
    async with AsyncSessionLocal() as s:
        assert (await s.get(Member, lab.member_id)).health_consent_at is None
    await lab.say("dormi 7h")
    await lab.tap("hc_yes:0")
    async with AsyncSessionLocal() as s:
        assert (await s.get(Member, lab.member_id)).health_consent_at is not None
    await lab.say("dormi 7h")
    assert await _count(lab, HealthLog) == 1


@db
async def test_withdrawing_health_consent_deletes_health_data(lab: Lab) -> None:
    lab.health.return_value = {
        "log_type": "water",
        "value": "2",
        "unit": "L",
        "notes": None,
        "days_ago": 0,
    }
    await lab.say("bebi 2L de água")
    assert await _count(lab, HealthLog) == 1
    out = await lab.say("retirar consentimento de saúde")
    assert "apaguei" in out.lower()
    assert await _count(lab, HealthLog) == 0
    async with AsyncSessionLocal() as s:
        assert (await s.get(Member, lab.member_id)).health_consent_at is None


def test_unsupported_health_reply_does_not_suggest_a_note() -> None:
    assert "nota" not in _t("health_unsupported", "pt").lower()


# ── 8. after "stop" the data rights still work ────────────────────────────────


async def _stop(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(Member).where(Member.id == lab.member_id).values(consent_state="rejected")
        )
        await s.commit()


@db
async def test_erase_and_export_work_after_stop(lab: Lab) -> None:
    await _stop(lab)
    out = await lab.say("apagar meus dados")
    assert out == _t("wipe_ask", "pt")
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["wipe", "keep"]
    await lab.tap("keep:0")
    out = await lab.say("exportar meus dados")
    assert out in (_t("dashboard_no_base_url", "pt"),) or "http" in out


def test_missing_health_key_in_production_raises_an_alert(monkeypatch) -> None:
    from alfred import crypto, observability

    seen: list[str] = []
    monkeypatch.setattr(crypto, "enabled", lambda: False)
    monkeypatch.setattr(observability, "alert", lambda name, **kw: seen.append(name))
    crypto.warn_if_unprotected("production")
    crypto.warn_if_unprotected("development")
    assert seen == ["crypto.no_key_in_production"]
