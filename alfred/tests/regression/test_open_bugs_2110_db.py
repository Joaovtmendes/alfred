# ruff: noqa: E501
"""Regression tests for the old open bugs closed on 21/10/2026 (code reading of 07/10 + hard tests).

One test (or more) per bug in ``tests/bugs.json``; each is marked ``@pytest.mark.bug``.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local, today_local
from alfred.models import (
    Expense,
    HabitLog,
    Member,
    Message,
    PartnerLink,
    RecurringItem,
    ServiceRecord,
    TripBudgetLine,
)
from alfred.settings import settings
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
TEN = now_local().replace(hour=10, minute=0, second=0, microsecond=0)


def _exp(amount: float, name: str | None, category: str = "overig") -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": category,
            "description": None, "type": "expense", "days_ago": 0}  # fmt: skip


def _cron():
    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).parent.parent.parent / "scripts" / "daily_cron.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def _inbound(lab: Lab, when: datetime) -> None:
    await lab.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id=f"in-{uuid.uuid4()}",
            household_id=lab.household_id,
            author_id=lab.member_id,
            direction="inbound",
            body="oi",
            created_at=when,
        )  # fmt: skip
    )


async def _count(lab: Lab, model, *where) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id, *where)
    )


# ── consultas e textos ──────────────────────────────────────────────────────


@db
@pytest.mark.bug("BUG-03")
async def test_hoje_alone_is_todays_spending(lab: Lab) -> None:
    out = await lab.say("hoje")
    assert "hoje" in out.lower() and "semana" not in out.lower()


@db
@pytest.mark.bug("A-55")
async def test_an_entry_without_a_shop_shows_the_category_name(lab: Lab) -> None:
    lab.expense.return_value = _exp(20, None)
    out = await lab.say("gastei 20")
    assert "overig" not in out and "Outros" in out


@db
@pytest.mark.bug("A-69")
async def test_maiores_gastos_typed_gives_the_top_list(lab: Lab) -> None:
    lab.expense.return_value = _exp(80, "Zara", "kleding")
    await lab.say("Zara 80")
    out = await lab.say("maiores gastos")
    assert "Zara" in out and "80,00" in out


@db
@pytest.mark.bug("A-70")
async def test_saldo_says_it_is_not_the_bank_balance(lab: Lab) -> None:
    lab.expense.return_value = _exp(20, "Jumbo", "supermarkt")
    await lab.say("Jumbo 20")
    assert "não o saldo do banco" in await lab.say("saldo")


@db
@pytest.mark.bug("A-70")
async def test_a_month_with_only_pending_bills_still_answers(lab: Lab) -> None:
    await lab.say("a pagar luz 120 dia 28")
    out = await lab.say("saldo")
    assert "120,00" in out


@db
@pytest.mark.bug("A-71")
async def test_days_in_the_black_warns_when_there_is_no_income(lab: Lab) -> None:
    lab.expense.return_value = _exp(20, "Jumbo", "supermarkt")
    await lab.say("Jumbo 20")
    assert "não há receita" in await lab.say("dias no positivo")


@db
@pytest.mark.bug("A-72")
async def test_the_reminder_text_offers_to_send_the_tikkie(lab: Lab) -> None:
    await lab.say("Pedro me deve 20")
    out = await lab.say("cobra o Pedro")
    assert "envie um Tikkie" not in out and "te mando um Tikkie" in out


# ── categorias pt-BR ────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-54")
async def test_the_old_portuguese_names_still_work_in_a_correction(lab: Lab) -> None:
    lab.expense.return_value = _exp(40, "Lojinha")
    await lab.say("Lojinha 40")
    out = await lab.say("isso é vestuário")
    assert "Roupas" in out


# ── guarda do modelo, palavras soltas, hábito ──────────────────────────────


@db
@pytest.mark.bug("A-28")
async def test_a_question_still_blocks_a_claim_that_something_was_done(lab: Lab) -> None:
    lab.llm_reply.return_value = "Apaguei o cinema, pode ficar tranquilo."
    out = await lab.say("você apagou o cinema?")
    assert "Apaguei" not in out


@db
@pytest.mark.bug("BUG-08")
async def test_a_dutch_compound_word_is_not_gibberish() -> None:
    from alfred.conversation import _keyboard_mash

    assert _keyboard_mash("asdfjklqwerty") and _keyboard_mash("ahshdjejabs")
    assert not _keyboard_mash("kunststof", "nl") and not _keyboard_mash("kkkkkkk")
    assert not _keyboard_mash("supermercado")


@db
@pytest.mark.bug("A-49")
async def test_a_real_habit_still_reaches_the_habit_model(lab: Lab) -> None:
    await lab.say("toquei violão")
    assert lab.habit.await_count == 1


# ── extrato ────────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-37")
async def test_skipped_statement_lines_are_reported(lab: Lab) -> None:
    out = await lab.doc(
        "extrato.csv",
        "Data;Descricao;Valor\n2026-10-01;Albert Heijn;-23,45\nxx;linha ruim;\n2026-10-02;Zero;0\n",
    )
    assert "2 linhas ficaram de fora" in out


@db
@pytest.mark.bug("A-37")
async def test_a_tab_file_is_accepted(lab: Lab) -> None:
    out = await lab.doc(
        "extrato.TAB",
        "Data;Descricao;Valor\n2026-10-01;Albert Heijn;-23,45\n",
        mime="application/octet-stream",
    )
    assert "Albert Heijn" in out or "Li seu extrato" in out


@db
@pytest.mark.bug("A-38")
async def test_same_amount_same_day_other_name_is_a_possible_duplicate(lab: Lab) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=23.45,
            merchant="mercado",
            category="supermarkt",
            status="paid",
            expense_date=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )  # fmt: skip
    )
    out = await lab.doc("extrato.csv", "Data;Descricao;Valor\n2026-10-01;Albert Heijn;-23,45\n")
    assert "Possíveis duplicados de lançamentos seus: 1" in out


# ── lote ───────────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-68")
async def test_a_batch_of_eight_can_be_undone(lab: Lab) -> None:
    lab.multi.return_value = [_exp(1 + i, f"loja{i}") for i in range(8)]
    await lab.say("loja0 1, loja1 2, loja2 3, loja3 4, loja4 5, loja5 6, loja6 7, loja7 8")
    await lab.tap(next(b for b, t in lab.buttons[-1] if b.startswith("batch_ok")))
    undo = next(b for b, t in lab.buttons[-1] if b.startswith("batch_undo"))
    assert "8" in await lab.tap(undo)
    assert await _count(lab, Expense) == 0
    assert "8" not in await lab.tap(undo)  # a second tap finds nothing


@db
@pytest.mark.bug("A-51")
async def test_a_sentence_with_pode_or_no_never_answers_the_draft(lab: Lab) -> None:
    lab.multi.return_value = [_exp(3, "café"), _exp(8, "padaria")]
    await lab.say("café 3 e padaria 8")
    lab.expense.return_value = None
    await lab.say("pode esperar")
    await lab.say("almoço no shopping")
    assert await _count(lab, Expense) == 0
    assert "Gravei" in await lab.say("pode confirmar") or await _count(lab, Expense) == 2


# ── painel ─────────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-17")
async def test_novo_link_do_painel_revokes_the_old_one(lab: Lab) -> None:
    await lab.say("meu dashboard")
    old = await lab.scalar(select(Member.dashboard_token).where(Member.id == lab.member_id))
    await lab.say("novo link do painel")
    new = await lab.scalar(select(Member.dashboard_token).where(Member.id == lab.member_id))
    assert old is not None and new is not None and new != old


# ── casal ──────────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-36")
async def test_the_inviter_can_cancel_an_invitation(lab: Lab) -> None:
    await lab.say("convidar parceiro")
    assert "cancelado" in (await lab.say("cancelar convite")).lower()
    open_links = await lab.scalar(
        select(func.count())
        .select_from(PartnerLink)
        .where(
            PartnerLink.inviter_id == lab.member_id, PartnerLink.status.in_(("invited", "pending"))
        )
    )
    assert open_links == 0


@db
@pytest.mark.bug("A-36")
async def test_a_batch_follows_always_home(lab: Lab, lab2: Lab) -> None:
    await lab.add(
        PartnerLink(
            inviter_id=lab.member_id,
            partner_id=lab2.member_id,
            code="TST" + uuid.uuid4().hex[:3].upper(),
            status="active",
            inviter_pct=50,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            accepted_at=datetime.now(UTC),
        )  # fmt: skip
    )
    await lab.say("sempre da casa: mercado")
    lab.multi.return_value = [_exp(30, "Jumbo", "supermarkt"), _exp(10, "Zara", "kleding")]
    await lab.say("Jumbo 30 e Zara 10")
    await lab.tap(next(b for b, t in lab.buttons[-1] if b.startswith("batch_ok")))
    shared = await lab.rows(
        select(Expense.merchant, Expense.shared).where(Expense.member_id == lab.member_id)
    )
    assert dict(shared) == {"Jumbo": True, "Zara": False}


# ── treino, hábitos ────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-62")
async def test_a_forgotten_draft_does_not_catch_the_edit_of_the_saved_plan(lab: Lab) -> None:
    from sqlalchemy import update

    from alfred.db import AsyncSessionLocal
    from alfred.models import PendingAction

    plan = "plano de treino:\nSegunda - Peito: Supino 4x10 60kg"
    await lab.say(plan)
    await lab.tap(next(b for b, t in lab.buttons[-1] if b.startswith("plan_ok")))
    await lab.say(plan.replace("60kg", "70kg"))  # a second draft, never confirmed
    async with AsyncSessionLocal() as s:
        await s.execute(update(PendingAction).values(expires_at=now_local() - timedelta(minutes=1)))
        await s.commit()
    out = await lab.say("troca o supino de segunda para 62 kg")
    assert "62 kg" in out and "60 kg" in out  # the saved plan (60) was edited, not the draft (70)


@db
@pytest.mark.bug("A-65")
async def test_the_streak_counts_by_the_amsterdam_date(lab: Lab) -> None:
    today = today_local()
    await lab.add(
        HabitLog(member_id=lab.member_id, activity="meditei", log_date=today),
        HabitLog(member_id=lab.member_id, activity="meditei", log_date=today - timedelta(days=1)),
    )
    with patch("alfred.conversation.today_local", lambda: today):
        out = await lab.say("quantos dias seguidos meditei")
    assert "2" in out


# ── serviços e viagem ──────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-77")
async def test_no_after_yes_removes_the_cooling_off_period(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, 180 €")
    await lab.tap(next(b for b, t in lab.buttons[-1] if b.startswith("svc_ok")))
    buttons = dict(lab.buttons[-1])
    yes = next(b for b in buttons if b.startswith("svc_wd_yes"))
    no = next(b for b in buttons if b.startswith("svc_wd_no"))
    await lab.tap(yes)
    assert "tirei" in (await lab.tap(no)).lower()
    until = await lab.scalar(
        select(ServiceRecord.withdrawal_until).where(ServiceRecord.member_id == lab.member_id)
    )
    assert until is None


@db
@pytest.mark.bug("A-78")
async def test_trip_lodging_is_called_lodging_and_the_overall_budget_shows(lab: Lab) -> None:
    await lab.say("criar viagem Roma 800")
    assert "Hospedagem" in await lab.say("orçamento da viagem hospedagem 300")
    await lab.say("orçamento da viagem passeios 100")
    out = await lab.say("orçamento da viagem")
    assert "Hospedagem" in out and "Passeios" in out and "800,00" in out
    assert await lab.scalar(select(func.count()).select_from(TripBudgetLine)) == 2


# ── avisos, limites ────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-46")
async def test_a_bill_to_pay_is_reminded_on_its_day_once(lab: Lab) -> None:
    await lab.say(f"a pagar luz 120 dia {today_local().day}")
    await _inbound(lab, TEN - timedelta(hours=1))
    sent: list[tuple[str, str]] = []

    async def fake_text(to, text):
        sent.append((to, text))

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_bill_reminders(TEN)
        await _cron().send_bill_reminders(TEN)  # idempotent
    mine = [t for to, t in sent if to == lab.phone]
    assert len(mine) == 1 and "vence hoje" in mine[0]


@db
@pytest.mark.bug("A-46")
async def test_a_fixed_bill_not_paid_gets_a_late_reminder(lab: Lab) -> None:
    await lab.add(
        RecurringItem(
            id=uuid.uuid4(),
            member_id=lab.member_id,
            household_id=lab.household_id,
            name="Aluguel",
            amount=1200,
            category="wonen",
            next_due_date=today_local() - timedelta(days=1),
            due_day=1,
        )  # fmt: skip
    )
    await _inbound(lab, TEN - timedelta(hours=1))
    sent: list[tuple[str, str]] = []

    async def fake_text(to, text):
        sent.append((to, text))

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_bill_reminders(TEN)
    assert any("venceu ontem" in t for to, t in sent if to == lab.phone)


@db
@pytest.mark.bug("A-81")
async def test_a_burst_of_messages_is_paused(lab: Lab, monkeypatch) -> None:
    monkeypatch.setattr(settings, "messages_per_minute", 3)
    for _ in range(4):
        await _inbound(lab, datetime.now(UTC))
    out = await lab.say("saldo")
    assert "muitas mensagens" in out
    await _inbound(lab, datetime.now(UTC))
    with pytest.raises(AssertionError):  # past the notice: silence for the minute
        await lab.say("saldo")


@db
@pytest.mark.bug("A-76")
async def test_the_same_thumb_twice_is_one_rating(lab: Lab) -> None:
    from alfred.models import ReplyFeedback

    await lab.tap("fb_up:chat")
    await lab.tap("fb_up:chat")
    assert await _count(lab, ReplyFeedback) == 1


@pytest.mark.bug("C-02")
def test_the_hard_test_script_opens_with_the_signup_not_with_sim() -> None:
    text = (Path(__file__).parent.parent.parent / "scripts" / "hard_test.py").read_text()
    opening = text[
        text.index("def abertura")
        if "def abertura" in text
        else text.index("Abertura pelo cadastro") :
    ]
    first_send = opening[opening.index("send(") :].split("\n", 1)[0]
    assert '"sim"' not in first_send
