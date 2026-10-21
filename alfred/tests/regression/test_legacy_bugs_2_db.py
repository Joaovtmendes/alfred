# ruff: noqa: E501
"""Regression tests for the legacy backlog, second batch (see test_legacy_bugs_db.py)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from alfred.conversation import _detect_language
from alfred.labels import category_label
from alfred.models import Appointment, Budget, ClientInvoice, Note, ScheduledJob
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


async def _count(lab: Lab, model) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id)
    )


# ── texto e rótulos (puros) ───────────────────────────────────────────────────


@pytest.mark.bug("A-14")
@pytest.mark.bug("B-002")
async def test_the_terms_link_opens_the_terms_themselves(client) -> None:
    # B-002 (hard test 18/10): the app page only had a link; it now goes straight to the Terms
    resp = await client.get("/terms", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "https://jofie.nl/terms/"


@pytest.mark.bug("A-54")
@pytest.mark.parametrize("word", ["Subscrições", "Rendimento", "Habitação", "Vestuário"])
@pytest.mark.xfail(strict=True, reason="A-54 aberto: remova junto com a correção")
def test_portuguese_labels_are_brazilian(word) -> None:
    labels = [
        category_label(c, "pt") for c in ("abonnement", "inkomen", "wonen", "kleding", "overig")
    ]
    assert word not in labels


@pytest.mark.bug("A-20")
@pytest.mark.parametrize("text", ["Bom dia", "bom dia!", "boa tarde"])
def test_portuguese_greetings_are_recognised(text) -> None:
    assert _detect_language(text) == "pt"


@pytest.mark.bug("A-20")
def test_a_word_containing_another_language_name_is_not_that_language() -> None:
    assert _detect_language("quanto custa o plano comercial?") != "fr"


# ── consentimento e comandos ──────────────────────────────────────────────────


@db
@pytest.mark.bug("A-41")
async def test_rescheduling_a_meeting_is_not_a_training_plan_edit(lab: Lab) -> None:
    out = await lab.say("muda a reunião de segunda para sexta")
    assert "Não entendi a mudança" not in out


@db
@pytest.mark.bug("A-42")
async def test_deleting_a_workout_does_not_cancel_a_future_appointment(lab: Lab) -> None:
    await lab.add(
        Appointment(
            member_id=lab.member_id,
            household_id=lab.household_id,
            title="Treino de perna",
            starts_at=datetime.now(UTC) + timedelta(days=3),
        )
    )
    await lab.say("apaga o treino")
    assert await _count(lab, Appointment) == 1


@db
@pytest.mark.bug("A-40")
async def test_agenda_with_a_title_and_time_creates_the_appointment(lab: Lab) -> None:
    await lab.say("agenda dentista amanhã às 14h")
    assert await _count(lab, Appointment) == 1


@db
@pytest.mark.bug("A-34")
async def test_a_utility_bill_is_not_a_client_invoice(lab: Lab) -> None:
    await lab.say("fatura de luz 80")
    assert await _count(lab, ClientInvoice) == 0


@db
@pytest.mark.bug("A-50")
async def test_a_photo_sent_as_a_document_is_not_called_a_csv(lab: Lab) -> None:
    out = await lab.doc("recibo.jpg", b"\xff\xd8\xff", mime="image/jpeg")
    assert "csv" not in out.lower()


@db
@pytest.mark.bug("A-60")
async def test_a_zero_budget_is_refused(lab: Lab) -> None:
    await lab.say("orçamento mercado 0")
    assert await _count(lab, Budget) == 0


@db
@pytest.mark.bug("A-63")
async def test_a_note_that_mentions_a_reminder_stays_a_note(lab: Lab) -> None:
    await lab.say("nota: lembrete tomar remédio às 08:00")
    assert await _count(lab, Note) == 1
    assert await _count(lab, ScheduledJob) == 0


@db
@pytest.mark.bug("A-64")
async def test_the_word_transporte_does_not_make_a_workout_reminder(lab: Lab) -> None:
    await lab.say("lembrete: pegar o transporte às 08:00")
    kinds = [
        r[0]
        for r in await lab.rows(
            select(ScheduledJob.job_type).where(ScheduledJob.member_id == lab.member_id)
        )
    ]
    assert "workout_reminder" not in kinds


@db
@pytest.mark.bug("A-80")
async def test_a_greeting_costs_at_most_one_model_call(lab: Lab) -> None:
    await lab.say("oi")
    calls = sum(
        m.await_count
        for m in (
            lab.expense,
            lab.habit,
            lab.health,
            lab.workout,
            lab.classify,
            lab.llm_reply,
            lab.multi,
        )
    )
    assert calls <= 1
