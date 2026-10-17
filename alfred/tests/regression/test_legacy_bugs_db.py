# ruff: noqa: E501
"""Regression tests for the legacy backlog (code-reading findings of 07/10, hard tests).

Each test states the behaviour the finding asks for. A test that passes means the bug is
fixed on master; one marked ``xfail(strict=True)`` documents a bug that is still open and
turns the build red the day the fix lands, so the mark and the registry get updated.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from alfred.models import Appointment, Expense, Iou, Member, ScheduledJob, Task, Trip
from tests.conftest import make_message
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str, category: str = "kleding", currency: str = "EUR") -> dict:
    return {"amount": amount, "currency": currency, "merchant": name, "category": category,
            "description": name, "type": "expense", "days_ago": 0}  # fmt: skip


async def _count(lab: Lab, model) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id)
    )


async def _category_of(lab: Lab, merchant: str) -> str | None:
    return await lab.scalar(
        select(Expense.category).where(
            Expense.member_id == lab.member_id, Expense.merchant == merchant
        )
    )


# ── privacy: apagar e exportar ────────────────────────────────────────────────


@db
@pytest.mark.bug("A-15")
@pytest.mark.bug("C-01")
@pytest.mark.parametrize(
    "text",
    [
        "excluir meus dados",
        "apagar dados",
        "deletar minha conta",
        "delete all data",
        "delete my account",
        "verwijder mijn account",
    ],
)
async def test_wipe_requests_in_other_words_ask_for_confirmation(lab: Lab, text) -> None:
    out = await lab.say(text)
    assert lab.buttons[-1], f"sem botão de confirmação: {out!r}"
    assert "suporte" not in out.lower() and "support" not in out.lower()


@db
@pytest.mark.bug("A-15")
async def test_export_in_other_words_gives_the_export(lab: Lab) -> None:
    out = await lab.say("quero exportar meus dados")
    assert "[llm]" not in out


@db
@pytest.mark.bug("A-16")
async def test_cancel_invalidates_the_wipe_button(lab: Lab) -> None:
    await lab.say("apagar meus dados")
    wipe_id = next(i for i, _ in lab.buttons[-1] if i.startswith("wipe"))
    keep_id = next(i for i, _ in lab.buttons[-1] if i.startswith("keep"))
    await lab.tap(keep_id)
    await lab.tap(wipe_id)
    still_there = await lab.scalar(
        select(func.count()).select_from(Member).where(Member.id == lab.member_id)
    )
    assert still_there == 1


@db
@pytest.mark.bug("A-15")
async def test_typed_yes_confirms_the_wipe_and_typed_no_cancels(lab: Lab) -> None:
    await lab.say("apagar meus dados")
    await lab.say("não")
    assert await lab.scalar(
        select(func.count()).select_from(Member).where(Member.id == lab.member_id)
    )
    await lab.say("apagar meus dados")
    await lab.say("sim")
    assert not await lab.scalar(
        select(func.count()).select_from(Member).where(Member.id == lab.member_id)
    )


# ── mídia e idioma ────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-19")
async def test_audio_gets_a_reply(lab: Lab) -> None:
    raw = {"type": "audio", "audio": {"id": "media1", "mime_type": "audio/ogg"}}
    out = await lab._run(make_message(body=None, raw=raw))
    assert out


@db
@pytest.mark.bug("A-22")
@pytest.mark.xfail(strict=True, reason="A-22 aberto: remova junto com a correção")
async def test_muda_pra_ingles_changes_the_language(lab: Lab) -> None:
    await lab.say("muda pra inglês")
    lang = await lab.scalar(select(Member.language).where(Member.id == lab.member_id))
    assert lang == "en"


@db
@pytest.mark.bug("A-22")
@pytest.mark.xfail(strict=True, reason="A-22 aberto: remova junto com a correção")
async def test_a_language_name_alone_does_not_change_the_language(lab: Lab) -> None:
    await lab.say("francês")
    lang = await lab.scalar(select(Member.language).where(Member.id == lab.member_id))
    assert lang == "pt"


# ── categorias ────────────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-23")
@pytest.mark.parametrize("text", ["Zara é lazer", "isso é lazer", "isso é transporte"])
@pytest.mark.xfail(strict=True, reason="A-23 aberto: remova junto com a correção")
async def test_category_names_the_chat_shows_are_accepted(lab: Lab, text) -> None:
    lab.expense.return_value = _exp(89, "Zara")
    await lab.say("Zara 89")
    before = await _category_of(lab, "Zara")
    await lab.say(text)
    assert await _category_of(lab, "Zara") != before


@db
@pytest.mark.bug("A-24")
@pytest.mark.xfail(strict=True, reason="A-24 aberto: remova junto com a correção")
async def test_an_expense_never_takes_the_income_category(lab: Lab) -> None:
    lab.expense.return_value = _exp(30, "Jumbo", "supermarkt")
    await lab.say("Jumbo 30")
    await lab.say("Jumbo é renda")
    assert await _category_of(lab, "Jumbo") != "inkomen"


# ── saldo, guarda do modelo, valores ──────────────────────────────────────────


@db
@pytest.mark.bug("A-27")
async def test_saldo_da_casa_is_not_the_personal_balance(lab: Lab) -> None:
    personal = await lab.say("saldo")
    house = await lab.say("saldo da casa")
    assert house != personal


@db
@pytest.mark.bug("A-28")
@pytest.mark.xfail(strict=True, reason="A-28 aberto: remova junto com a correção")
async def test_a_question_about_stored_data_is_not_swallowed_by_the_guard(lab: Lab) -> None:
    lab.llm_reply.return_value = (
        "Tudo o que está anotado sobre você fica guardado na Europa, registrado só para você."
    )
    out = await lab.say("meus dados ficam guardados onde?")
    assert "Europa" in out  # the model's own answer reaches the member


@db
@pytest.mark.bug("A-29")
async def test_a_negative_amount_is_not_recorded_as_an_expense(lab: Lab) -> None:
    lab.expense.return_value = _exp(-15, "Reembolso", "overig")
    await lab.say("reembolso -15")
    assert await _count(lab, Expense) == 0


@db
@pytest.mark.bug("A-79")
@pytest.mark.xfail(strict=True, reason="A-79 aberto: remova junto com a correção")
async def test_the_dollar_refusal_does_not_end_with_a_question_mark(lab: Lab) -> None:
    lab.expense.return_value = _exp(50, "Walmart", "supermarkt", currency="USD")
    out = await lab.say("gastei 50 dollars")
    assert not out.strip().endswith("?")


# ── dívidas, extrato, agenda, lembretes ───────────────────────────────────────


@db
@pytest.mark.bug("A-31")
@pytest.mark.xfail(strict=True, reason="A-31 aberto: remova junto com a correção")
async def test_the_article_is_not_part_of_the_name(lab: Lab) -> None:
    await lab.say("a Ana me deve 40")
    person = await lab.scalar(select(Iou.person).where(Iou.member_id == lab.member_id))
    assert person == "Ana"


@db
@pytest.mark.bug("A-37")
@pytest.mark.xfail(strict=True, reason="A-37 aberto: remova junto com a correção")
async def test_a_portuguese_statement_header_is_recognised(lab: Lab) -> None:
    out = await lab.doc("extrato.csv", "Data;Descricao;Valor\n2026-10-01;Albert Heijn;-23,45\n")
    assert "csv" not in out.lower() or "reconheci" not in out.lower()
    assert "Albert Heijn" in out or "1" in out


@db
@pytest.mark.bug("A-39")
@pytest.mark.xfail(strict=True, reason="A-39 aberto: remova junto com a correção")
async def test_a_task_with_a_time_is_a_task_not_an_appointment(lab: Lab) -> None:
    await lab.say("tarefa: pagar o dentista amanhã às 14h")
    assert await _count(lab, Task) == 1
    assert await _count(lab, Appointment) == 0


@db
@pytest.mark.bug("A-43")
@pytest.mark.xfail(strict=True, reason="A-43 aberto: remova junto com a correção")
async def test_a_reminder_with_tomorrow_is_not_a_daily_reminder(lab: Lab) -> None:
    await lab.say("lembrete: ligar pro dentista amanhã às 14:00")
    jobs = await lab.rows(select(ScheduledJob).where(ScheduledJob.member_id == lab.member_id))
    assert not any(j[0].days_mask == 127 for j in jobs)


@db
@pytest.mark.bug("A-47")
@pytest.mark.xfail(strict=True, reason="A-47 aberto: remova junto com a correção")
async def test_a_health_question_without_a_question_mark_does_not_log(lab: Lab) -> None:
    await lab.say("quantas horas dormi esta semana")
    assert lab.health.await_count == 0


@db
@pytest.mark.bug("A-48")
@pytest.mark.xfail(strict=True, reason="A-48 aberto: remova junto com a correção")
async def test_criar_viagem_with_a_budget_and_no_symbol(lab: Lab) -> None:
    await lab.say("criar viagem Roma 800")
    trip = await lab.rows(
        select(Trip.destination, Trip.budget).where(Trip.member_id == lab.member_id)
    )
    assert trip and trip[0][0].lower() == "roma" and trip[0][1] == 800


@db
@pytest.mark.bug("A-49")
@pytest.mark.xfail(strict=True, reason="A-49 aberto: remova junto com a correção")
async def test_short_commands_never_reach_the_habit_model(lab: Lab) -> None:
    for text in ("oi", "voltei", "apaga"):
        await lab.say(text)
    assert lab.habit.await_count == 0


# ── texto e comandos ──────────────────────────────────────────────────────────


@db
@pytest.mark.bug("A-56")
@pytest.mark.xfail(strict=True, reason="A-56 aberto: remova junto com a correção")
async def test_singular_has_no_plural_noun(lab: Lab) -> None:
    lab.expense.return_value = _exp(12, "Padaria", "supermarkt")
    await lab.say("padaria 12")
    out = await lab.say("últimas 1 despesas")
    assert "1 despesas" not in out


@db
@pytest.mark.bug("A-59")
async def test_a_sentence_starting_with_dashboard_is_not_the_link_command(lab: Lab) -> None:
    await lab.say("dashboard está lento")
    assert not lab.cta


@db
@pytest.mark.bug("A-69")
@pytest.mark.xfail(strict=True, reason="A-69 aberto: remova junto com a correção")
async def test_meus_gastos_is_a_command(lab: Lab) -> None:
    out = await lab.say("meus gastos")
    assert "[llm]" not in out


@db
@pytest.mark.bug("A-75")
@pytest.mark.xfail(strict=True, reason="A-75 aberto: remova junto com a correção")
async def test_stop_with_a_full_stop_still_stops(lab: Lab) -> None:
    await lab.say("stop.")
    state = await lab.scalar(select(Member.consent_state).where(Member.id == lab.member_id))
    assert state == "rejected"


@db
@pytest.mark.bug("BUG-08")
@pytest.mark.xfail(strict=True, reason="BUG-08 aberto: remova junto com a correção")
async def test_gibberish_is_rejected_without_calling_the_model(lab: Lab) -> None:
    await lab.say("asdfjklqwerty")
    assert lab.llm_reply.await_count == 0 and lab.classify.await_count == 0


_ = (uuid, datetime, UTC)
