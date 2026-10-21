# ruff: noqa: E501
"""Regression tests for the manual hard test of 18–20/10/2026 (checklist + J's notes).

Every bug found there has an ID in ``tests/bugs.json`` (B-002 … B-053) and at least one test
here marked ``@pytest.mark.bug``. The changes J asked for (M-01 … M-28 in the report) are
pinned by the unmarked tests at the end, so a later edit cannot undo them silently.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local, today_local
from alfred.conversation import _t
from alfred.models import (
    Appointment,
    Budget,
    CalendarOptIn,
    Expense,
    Goal,
    HabitLog,
    HealthLog,
    Member,
    Note,
    RecurringItem,
    ScheduledJob,
    ServiceRecord,
    Task,
    Trip,
    TripBudgetLine,
    WorkoutLoad,
)
from alfred.parsing import parse_period
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str, category: str = "overig", kind: str = "expense") -> dict:
    return {"amount": amount, "currency": "EUR", "merchant": name, "category": category,
            "description": name, "type": kind, "days_ago": 0}  # fmt: skip


async def _count(lab: Lab, model) -> int:
    return await lab.scalar(
        select(func.count()).select_from(model).where(model.member_id == lab.member_id)
    )


def _row(lab: Lab, amount: float, name: str, category: str, *, kind: str = "expense", days_ago: int = 0, status: str = "paid", source: str = "manual") -> Expense:  # fmt: skip
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        currency="EUR",
        merchant=name,
        category=category,
        description=name,
        expense_date=now_local() - timedelta(days=days_ago),
        status=status if kind == "expense" else "received",
        source=source,
    )


# ── the model must never claim an action that did not happen ─────────────────


@db
@pytest.mark.bug("B-004")
@pytest.mark.parametrize(
    "claim",
    [
        "Apaguei: €1.200,00 em cinema (18/10).",
        "Apaguei a última despesa: café, €3,50.",
        "Nota deletada: senha do wifi é Casa123",
        "Deletei a meta correr 10 km.",
        "Você foi desconectado. Para voltar, é só chamar de novo.",
        "Certo, vou parar de enviar mensagens.",
        "Confirmado: Luz €120,00 paga em 20/10.",
        "Atualizado: €400,00 por mês em Supermercado.",
        "Entendi, seu saldo é €300,00.",
        "Gravei os 3 lançamentos: mercado €45,00",
        "Combinado! Vou te lembrar de reunião amanhã, às 09:00.",
        "Entendi este contrato:\n• Água: Vitens · €20,00 por mês",
    ],
)
async def test_a_free_reply_that_claims_an_action_is_replaced(lab: Lab, claim: str) -> None:
    lab.llm_reply.return_value = claim
    out = await lab.say("me conta uma coisa sobre a vida")
    assert out != claim


@db
@pytest.mark.bug("B-004")
async def test_typed_desfazer_asks_before_deleting_the_last_entry(lab: Lab) -> None:
    await lab.add(_row(lab, 3.5, "café", "restaurant"))
    out = await lab.say("Desfazer")
    assert "Apagar €3,50" in out and await _count(lab, Expense) == 1
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["undo", "ok"]


# ── money: corrections, balance, currencies, periods ──────────────────────────


@db
@pytest.mark.bug("B-005")
async def test_a_correction_names_the_entry_not_its_category(lab: Lab) -> None:
    lab.expense.return_value = {
        **_exp(25, None, "entertainment"),
        "merchant": None,
        "description": "cinema",
    }
    await lab.say("cinema 25")
    out = await lab.say("errei, foram 26")
    assert out.startswith("Corrigido: cinema") and "Lazer" not in out


@db
@pytest.mark.bug("B-006")
async def test_a_bank_balance_is_not_pretended_or_stored(lab: Lab) -> None:
    out = await lab.say("saldo da conta 300")
    assert out == _t("bank_balance_not_tracked", "pt")
    assert await _count(lab, Expense) == 0


@db
@pytest.mark.bug("B-007")
async def test_only_the_item_next_to_the_dollar_is_refused_and_in_one_line(lab: Lab) -> None:
    lab.multi.return_value = [
        {**_exp(20, "mercado", "supermarkt"), "currency": "USD"},
        {**_exp(10, "táxi", "transport"), "currency": "USD"},
    ]
    out = await lab.say("mercado 20 e 10 dólares de táxi")
    assert "mercado" not in out.split("Não guardei")[-1]
    assert out.count("Não guardei") == 1 and "táxi" in out


@db
@pytest.mark.bug("B-009")
async def test_saldo_de_setembro_is_september(lab: Lab) -> None:
    await lab.add(_row(lab, 40, "Jumbo", "supermarkt"))
    out = await lab.say("saldo de setembro")
    assert "Outubro" not in out and "setembro" in out.lower()


@pytest.mark.bug("B-009")
@pytest.mark.bug("A-25")
def test_a_bare_year_is_that_year() -> None:
    today = today_local()
    w = parse_period("gastos em 2025", today)
    assert w is not None and w.kind == "year" and w.start.year == 2025 and w.end.year == 2026


@pytest.mark.bug("B-010")
def test_last_n_days_and_today_and_yesterday() -> None:
    today = today_local()
    w = parse_period("gastos dos ultimos 7 dias", today)
    assert (
        w.kind == "days"
        and w.start == today - timedelta(days=6)
        and w.end == today + timedelta(days=1)
    )
    w2 = parse_period("quanto gastei hoje e ontem", today)
    assert w2.kind == "today_yesterday" and w2.start == today - timedelta(days=1)


@db
@pytest.mark.bug("B-011")
@pytest.mark.bug("A-26")
async def test_quanto_recebi_lists_the_income(lab: Lab) -> None:
    await lab.add(_row(lab, 3000, "salário", "inkomen", kind="income"))
    out = await lab.say("quanto recebi este mês?")
    assert "3.000,00" in out


@db
@pytest.mark.bug("B-012")
async def test_minhas_ultimas_despesas_and_ultimas_receitas(lab: Lab) -> None:
    await lab.add(
        _row(lab, 12, "Padaria", "supermarkt"), _row(lab, 3000, "Salário", "inkomen", kind="income")
    )
    out = await lab.say("minhas últimas despesas")
    assert "Padaria" in out and "Salário" not in out
    out2 = await lab.say("últimas receitas")
    assert "Salário" in out2 and "Padaria" not in out2


@db
@pytest.mark.bug("B-013")
@pytest.mark.bug("A-52")
async def test_an_analysis_question_goes_to_the_analysis_not_the_month_comparison(lab: Lab) -> None:
    from unittest.mock import AsyncMock, patch

    spec = {
        "supported": True,
        "spec": {"metric": "average", "group_by": "month", "period": "last_3_months"},
    }
    with patch("alfred.analysis.extract_analysis_spec", AsyncMock(return_value=spec)) as ex:
        out = await lab.say("média de gastos por mês nos últimos 3 meses")
    assert ex.await_count == 1 and "Comparação de despesas" not in out
    assert lab.classify.await_count == 0  # the coarse classifier is not asked first


@pytest.mark.bug("B-016")
def test_last_six_months_include_the_month_in_progress() -> None:
    from alfred.analysis import Spec, windows

    cur, _ = windows(Spec(period="last_6_months"), today_local())
    assert cur.start <= today_local().replace(day=1) and cur.end > today_local()


@db
@pytest.mark.bug("B-017")
async def test_orcamento_de_400_para_mercado_sets_the_budget(lab: Lab) -> None:
    await lab.say("orçamento mercado 85")
    await lab.say("orçamento de 400 para mercado")
    limit = await lab.scalar(select(Budget.monthly_limit).where(Budget.member_id == lab.member_id))
    assert float(limit) == 400


# ── fixed bills, pending bills, debts ─────────────────────────────────────────


@db
@pytest.mark.bug("B-018")
async def test_a_day_word_without_number_asks_the_day(lab: Lab) -> None:
    out = await lab.say("internet 45 todo mês dia DIA")
    assert "Em que dia" in out and await _count(lab, RecurringItem) == 0


@db
@pytest.mark.bug("B-018")
async def test_a_fixed_bill_typed_again_offers_to_change_the_amount(lab: Lab) -> None:
    await lab.say("internet 45 todo mês dia 10")
    out = await lab.say("internet 50 todo mês dia 10")
    assert "Quer trocar para €50,00" in out
    (upd, keep) = lab.buttons[-1]
    assert upd[0].startswith("rec_upd:") and keep[0].startswith("rec_keep:")
    await lab.tap(upd[0])
    amount = await lab.scalar(
        select(RecurringItem.amount).where(RecurringItem.member_id == lab.member_id)
    )
    assert float(amount) == 50 and await _count(lab, RecurringItem) == 1


@db
@pytest.mark.bug("B-019")
async def test_an_annual_bill_shows_next_years_date(lab: Lab) -> None:
    out = await lab.say("seguro 480 anual")
    assert str(today_local().year + 1) in out


@db
@pytest.mark.bug("B-020")
@pytest.mark.bug("A-33")
async def test_paguei_asks_when_a_pending_bill_and_a_fixed_bill_share_the_name(lab: Lab) -> None:
    await lab.say("a pagar luz 120 dia 28")
    await lab.say("luz 80 todo mês dia 10")
    out = await lab.say("paguei a luz")
    assert "€120,00" in out and "€80,00" in out and "conta fixa" in out
    out2 = await lab.say("paguei 80 da luz")
    assert "Luz" in out2
    paid = await lab.scalar(
        select(func.count())
        .select_from(Expense)
        .where(Expense.member_id == lab.member_id, Expense.status == "paid", Expense.amount == 80)
    )
    assert paid == 1


@db
@pytest.mark.bug("B-021")
async def test_a_payment_equal_to_one_debt_keeps_the_rest_open(lab: Lab) -> None:
    await lab.say("Pedro me deve 10")
    await lab.say("Pedro me deve 25,50")
    out = await lab.say("Pedro pagou 10")
    assert "25,50" in out and "Nada mais em aberto" not in out


@db
@pytest.mark.bug("B-021")
async def test_paguei_o_lucas_says_that_I_paid(lab: Lab) -> None:
    await lab.say("devo ao Lucas 25")
    out = await lab.say("paguei o Lucas")
    assert "você pagou" in out and "Lucas pagou" not in out


@db
@pytest.mark.bug("B-022")
@pytest.mark.bug("A-73")
async def test_what_you_sent_yesterday_is_yesterday_only(lab: Lab) -> None:
    out = await lab.say("o que você enviou ontem")
    assert out == _t("outbox_empty", "pt")  # nothing was sent yesterday to a new member


# ── agenda and reminders ──────────────────────────────────────────────────────


@db
@pytest.mark.bug("B-023")
async def test_agenda_with_title_and_time_creates_and_the_lead_in_days_is_cut(lab: Lab) -> None:
    await lab.say("agenda dentista amanhã às 14h")
    await lab.say("revisão do carro dia 28 às 9h avisa 1 dia antes")
    rows = await lab.rows(
        select(Appointment.title, Appointment.remind_before_minutes).where(
            Appointment.member_id == lab.member_id
        )
    )
    titles = {r[0]: r[1] for r in rows}
    assert "Dentista" in titles
    assert "Revisão do carro" in titles and titles["Revisão do carro"] == 1440


@db
@pytest.mark.bug("B-024")
async def test_agenda_do_mes_lists_until_the_end_of_the_month(lab: Lab) -> None:
    out = await lab.say("agenda do mês")
    assert out != _t("agenda_list_help", "pt")


@db
@pytest.mark.bug("B-025")
async def test_lembrete_with_9h_is_created(lab: Lab) -> None:
    out = await lab.say("lembrete: reunião às 9h")
    jobs = await lab.rows(
        select(ScheduledJob.time_of_day).where(ScheduledJob.member_id == lab.member_id)
    )
    assert [j[0] for j in jobs] == ["09:00"] and "09:00" in out


@pytest.mark.bug("B-026")
def test_a_reminder_inside_the_window_is_plain_text() -> None:
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "daily_cron", Path(__file__).resolve().parents[2] / "scripts" / "daily_cron.py"
    )
    cron = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cron)
    assert cron.reminder_body("tomar vitamina", "pt") == "⏰ Lembrete: tomar vitamina"
    src = (Path(__file__).resolve().parents[2] / "scripts" / "daily_cron.py").read_text()
    assert "seen >= cutoff" in src and "send_text(r.wa_phone, body)" in src


@db
@pytest.mark.bug("B-027")
async def test_an_ambiguous_reminder_cancel_asks_which(lab: Lab) -> None:
    await lab.say("lembrete: tomar o remédio às 08:00")
    await lab.say("lembrete: tomar vitamina às 09:00")
    out = await lab.say("cancela lembrete tomar")
    assert "Qual deles" in out
    active = await lab.scalar(
        select(func.count())
        .select_from(ScheduledJob)
        .where(ScheduledJob.member_id == lab.member_id, ScheduledJob.active.is_(True))
    )
    assert active == 2


# ── notes, tasks, goals, habits, health ───────────────────────────────────────


@db
@pytest.mark.bug("B-028")
async def test_apaga_a_nota_do_wifi_really_deletes_it(lab: Lab) -> None:
    await lab.say("nota: senha do wifi é Casa123")
    out = await lab.say("apaga a nota do wifi")
    assert "apaguei" in out.lower() and await _count(lab, Note) == 0


@db
@pytest.mark.bug("B-029")
async def test_a_task_is_deleted_by_name_or_number(lab: Lab) -> None:
    await lab.say("tarefa: comprar pão")
    await lab.say("tarefa: ligar pro banco")
    await lab.say("exclui a tarefa 1")
    await lab.say("remover tarefa ligar pro banco")
    assert await _count(lab, Task) == 0
    assert await lab.say("minhas tarefas") == _t("tasks_list_empty", "pt")


@db
@pytest.mark.bug("B-030")
async def test_apaga_a_meta_removes_it_from_the_list(lab: Lab) -> None:
    await lab.say("meta: correr 10 km")
    out = await lab.say("apaga a meta correr 10 km")
    assert "tirei a meta" in out
    active = await lab.scalar(
        select(func.count())
        .select_from(Goal)
        .where(Goal.member_id == lab.member_id, Goal.active.is_(True))
    )
    assert active == 0


@db
@pytest.mark.bug("B-031")
async def test_meditei_hoje_question_is_answered_from_the_log(lab: Lab) -> None:
    out = await lab.say("meditei hoje?")
    assert out.startswith("Ainda não tenho") and await _count(lab, HabitLog) == 0
    await lab.say("meditei hoje")
    assert (await lab.say("meditei hoje?")).startswith("Sim")


@db
@pytest.mark.bug("B-032")
async def test_agua_de_hoje_and_sono_medio_are_questions(lab: Lab) -> None:
    assert await lab.say("água de hoje") == _t("health_water_empty", "pt")
    assert await lab.say("sono médio") == _t("health_sleep_empty", "pt")
    assert await _count(lab, HabitLog) == 0 and await _count(lab, HealthLog) == 0


@db
@pytest.mark.bug("B-033")
@pytest.mark.bug("A-61")
async def test_body_weight_is_not_an_exercise_load(lab: Lab) -> None:
    out = await lab.say("peso corporal 75 kg")
    assert out == _t("health_unsupported", "pt") and await _count(lab, WorkoutLoad) == 0
    out2 = await lab.say("troca o supino para 62 kg")
    assert "De qual dia" in out2


@db
@pytest.mark.bug("B-034")
async def test_a_workout_without_duration_has_no_stray_comma(lab: Lab) -> None:
    lab.workout.return_value = {
        "activity_type": "strength",
        "duration_minutes": None,
        "distance_km": None,
    }
    out = await lab.say("fiz musculação")
    assert ", ." not in out and ", ," not in out


@db
@pytest.mark.bug("B-035")
async def test_word_and_number_is_an_expense_even_when_the_model_says_no(lab: Lab) -> None:
    lab.expense.return_value = None
    await lab.say("notebook 1210")
    amount = await lab.scalar(select(Expense.amount).where(Expense.member_id == lab.member_id))
    assert amount == 1210


@db
@pytest.mark.bug("B-035")
async def test_training_words_never_get_the_expense_example(lab: Lab) -> None:
    for text in ("fui à academia 1 hora", "carga supino 62"):
        out = await lab.say(text)
        assert "Mercado 20" not in out
    assert "kg" in await lab.say("carga supino 62")


@db
@pytest.mark.bug("B-054")
async def test_a_command_with_a_number_is_never_a_bare_expense(lab: Lab) -> None:
    lab.expense.return_value = None
    for text in (
        "corrige para 25",
        "o primeiro foi 40",
        "tira o 5",
        "troca o primeiro para 4",
        "gastos em 2025",
    ):
        await lab.say(text)
    assert await _count(lab, Expense) == 0


@db
@pytest.mark.bug("B-036")
async def test_evolucao_dos_gastos_is_not_a_training_load(lab: Lab) -> None:
    out = await lab.say("evolução dos gastos")
    assert "cargas anotadas" not in out


# ── trips, receipts, services, contracts, statements ──────────────────────────


@db
@pytest.mark.bug("B-038")
async def test_the_trip_line_has_its_own_row_and_the_planned_budget_remaining(lab: Lab) -> None:
    await lab.say("criar viagem Lisboa")
    trip_id = await lab.scalar(select(Trip.id).where(Trip.member_id == lab.member_id))
    await lab.add(
        TripBudgetLine(member_id=lab.member_id, trip_id=trip_id, category="restaurant", amount=500)
    )
    lab.expense.return_value = _exp(45, "jantar", "restaurant")
    out = await lab.say("jantar 45")
    assert "\n✈️ Viagem Lisboa: €45,00" in out and "restante €455,00" in out and "[" not in out


@pytest.mark.bug("B-039")
def test_a_receipt_from_a_supermarket_goes_to_supermarket() -> None:
    from alfred.receipt import item_from_reading

    item, _ = item_from_reading(
        {"merchant": "Supermarkt De Linde", "total": "21,10", "category": "groceries"}
    )
    assert item["category"] == "supermarkt"


@pytest.mark.bug("B-041")
def test_one_entry_is_singular() -> None:
    assert _t("batch_undone_one", "pt", n=1) == "Desfeito: 1 lançamento apagado."
    assert "1 lançamento importado" in _t("imp_done_one", "pt", n=1)


@db
@pytest.mark.bug("B-042")
async def test_saving_a_service_says_whether_the_cost_went_in(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, conserto da pia., 180 €, garantia 12 meses")
    rec_id = await lab.scalar(
        select(ServiceRecord.id).where(ServiceRecord.member_id == lab.member_id)
    )
    out = await lab.tap(f"svc_exp:{rec_id}")
    assert ".." not in out and "€180,00" in out and "despesas" in out


@db
@pytest.mark.bug("B-043")
async def test_an_old_button_says_it_was_used_not_deleted(lab: Lab) -> None:
    await lab.say("guarda serviço: encanador, 180 €, garantia 12 meses")
    rec_id = await lab.scalar(
        select(ServiceRecord.id).where(ServiceRecord.member_id == lab.member_id)
    )
    await lab.tap(f"svc_ok:{rec_id}")
    assert await lab.tap(f"svc_ok:{rec_id}") == _t("button_used", "pt")


@db
@pytest.mark.bug("B-045")
async def test_a_photo_sent_as_a_document_is_read_as_a_photo(lab: Lab) -> None:
    await lab.doc("recibo-mercado.jpg", b"\xff\xd8\xff", mime="image/jpeg")
    assert lab.reading.await_count == 1


@db
@pytest.mark.bug("B-046")
@pytest.mark.bug("B-047")
@pytest.mark.parametrize(
    "text", ["guarda serviço:", "guarda serviço: teste aviso, garantia 1 mes, DD/MM"]
)
async def test_an_empty_or_placeholder_service_is_refused(lab: Lab, text: str) -> None:
    assert await lab.say(text) == _t("svc_bad", "pt")
    assert await _count(lab, ServiceRecord) == 0


@db
@pytest.mark.bug("B-048")
async def test_a_contract_with_an_end_date_in_the_past_says_so(lab: Lab) -> None:
    assert await lab.say("contrato energia Vattenfall até 01/01/2020") == _t("home_past", "pt")
    assert "Quando termina" in await lab.say("contrato energia Vattenfall 120 por mês")  # F14-16


@db
@pytest.mark.bug("B-049")
async def test_rent_typed_by_hand_is_a_possible_duplicate_of_the_statement_line(lab: Lab) -> None:
    day = today_local().replace(day=1)
    await lab.add(_row(lab, 1250, "aluguel", "wonen"))
    csv = (
        '"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)","Mutatiesoort","Mededelingen"\n'
        f'"{day:%Y%m%d}","Woningstichting Huur","NL01INGB0000000001","NL02RABO0000000002","IC","Af","1250,00","Incasso","Huur"\n'
    )
    out = await lab.doc("extrato-ing.csv", csv)
    assert "Possíveis duplicados" in out or "possíve" in out.lower()


@db
@pytest.mark.bug("B-050")
async def test_an_import_that_blows_a_budget_says_so(lab: Lab) -> None:
    await lab.say("orçamento mercado 20")
    day = today_local()
    csv = (
        '"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)","Mutatiesoort","Mededelingen"\n'
        f'"{day:%Y%m%d}","Albert Heijn 1234","NL01INGB0000000001","","BA","Af","23,45","Betaalautomaat","Pas"\n'
    )
    await lab.doc("extrato-ing.csv", csv)
    ok = next(b[0] for b in lab.buttons[-1] if b[0].startswith("imp_ok"))
    out = await lab.tap(ok)
    assert "orçamento de Supermercado" in out


@db
@pytest.mark.bug("B-051")
@pytest.mark.parametrize("text", ["parar de enviar", "Stop."])
async def test_stop_phrases_really_stop(lab: Lab, text: str) -> None:
    await lab.say(text)
    state = await lab.scalar(select(Member.consent_state).where(Member.id == lab.member_id))
    assert state == "rejected"


@db
@pytest.mark.bug("B-051")
async def test_sair_explains_stop_and_erase(lab: Lab) -> None:
    out = await lab.say("sair")
    assert "STOP" in out and "apagar meus dados" in out


@db
@pytest.mark.bug("B-053")
async def test_packing_count_names_things(lab: Lab) -> None:
    await lab.say("criar viagem Bali")
    out = await lab.say("bagagem: passaporte, carregador, adaptador")
    assert "3 coisas" in out


# ── changes J asked for (M-xx in the report) ──────────────────────────────────


@db
async def test_m01_empty_period_reads_naturally(lab: Lab) -> None:
    out = await lab.say("resumo")
    assert "(" not in out and out.startswith("Não encontrei")


@db
async def test_m02_a_greeting_uses_the_name_and_no_model(lab: Lab) -> None:
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.preferred_name = "João"
        await s.commit()
    out = await lab.say("oi")
    assert out.startswith("Oi, João!") and lab.llm_reply.await_count == 0


@db
async def test_m03_m04_language_is_confirmed_or_chosen_from_the_menu(lab: Lab) -> None:
    await lab.say("inglês")
    assert await lab.scalar(select(Member.language).where(Member.id == lab.member_id)) == "pt"
    assert lab.buttons[-1][0][0] == "lang_set:en"
    await lab.say("mudar idioma")  # F02-33: a list with one row per language
    assert [r[1] for r in lab.buttons[-1]] == [
        "Português",
        "Nederlands",
        "English",
        "Français",
        "Deutsch",
    ]
    await lab.say("3")  # a typed number still works
    assert await lab.scalar(select(Member.language).where(Member.id == lab.member_id)) == "en"


@db
async def test_m05_a_negative_amount_can_become_income(lab: Lab) -> None:
    await lab.say("reembolso -15")
    yes = lab.buttons[-1][0][0]
    assert yes.startswith("neg_in:") and await _count(lab, Expense) == 0
    await lab.tap(yes)
    kind = await lab.scalar(
        select(Expense.transaction_type).where(Expense.member_id == lab.member_id)
    )
    assert kind == "income"


@db
async def test_m06_a_big_amount_is_confirmed_not_refused(lab: Lab) -> None:
    lab.expense.return_value = _exp(2_000_000, "iate", "overig")
    out = await lab.say("gastei 2000000 no iate")
    assert "2.000.000,00" in out and await _count(lab, Expense) == 1


@db
async def test_m10_no_feedback_buttons(lab: Lab) -> None:
    lab.llm_reply.return_value = "x" * 300
    await lab.say("explique como organizar meu orçamento")
    assert lab.buttons[-1] == []


@db
async def test_m11_budgets_show_left_or_over(lab: Lab) -> None:
    await lab.say("orçamento mercado 85")
    await lab.add(_row(lab, 34.45, "Jumbo", "supermarkt"))
    out = await lab.say("meus orçamentos")
    assert "faltam €50,55" in out and "fecha o mês" not in out


@db
async def test_m15_a_password_note_gets_a_tip(lab: Lab) -> None:
    out = await lab.say("nota: senha do wifi é Casa123")
    assert "gerenciador de senhas" in out


@db
async def test_m20_criar_viagem_with_a_bare_budget(lab: Lab) -> None:
    await lab.say("criar viagem Roma 800")
    rows = await lab.rows(
        select(Trip.destination, Trip.budget).where(Trip.member_id == lab.member_id)
    )
    assert rows and rows[0][0] == "Roma" and float(rows[0][1]) == 800


@db
async def test_m22_a_replaced_contract_is_named(lab: Lab) -> None:
    for text in (
        "contrato energia Vattenfall 120 por mês até 31/12/2030",
        "contrato energia Eneco 95 até 30/06/2031",
    ):
        await lab.say(text)
        ok = lab.buttons[-1][0][0]
        out = await lab.tap(ok)
    assert "Vattenfall" in out and "substitui" in out


@db
async def test_m23_prazos_without_tikkie_and_with_buttons(lab: Lab) -> None:
    out = await lab.say("prazos")
    assert "Tikkie" not in out
    assert [b[0] for b in lab.buttons[-1]] == ["cal_on:0", "cal_no:0"]
    await lab.tap("cal_on:0")
    assert await _count(lab, CalendarOptIn) == 1


@db
async def test_m25_start_brings_a_signed_up_member_back(lab: Lab) -> None:
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.disclosure_accepted_at = datetime.now(UTC)
        await s.commit()
    await lab.say("stop")
    out = await lab.say("start")
    assert out == _t("resumed_welcome", "pt")
    assert (
        await lab.scalar(select(Member.consent_state).where(Member.id == lab.member_id))
        == "accepted"
    )


@db
async def test_m27_a_purchase_without_price_asks_the_price(lab: Lab) -> None:
    out = await lab.say("comprei 2 kg de arroz")
    assert out.startswith("Quanto custou arroz")


@pytest.mark.bug("B-055")
def test_a_person_called_marco_is_not_march() -> None:
    today = datetime(2026, 10, 21).date()
    assert parse_period("quanto gastei com o marco", today) is None
    assert parse_period("gastos de marco", today).start.month == 3


@db
@pytest.mark.bug("B-056")
async def test_move_trip_and_itinerary_lines_never_create_an_appointment(lab: Lab) -> None:
    for text in (
        "muda o oculista para sexta às 15h",
        "criar viagem Lisboa dia 25 às 10h",
        "roteiro amanhã 10:00 museu",
    ):
        await lab.say(text)
    assert await _count(lab, Appointment) == 0


@db
@pytest.mark.bug("B-056")
async def test_moving_an_unknown_appointment_says_so(lab: Lab) -> None:
    out = await lab.say("muda o oculista para sexta às 15h")
    assert "oculista" in out and "agenda" in out
    assert await _count(lab, Appointment) == 0


@db
@pytest.mark.bug("B-056")
async def test_an_itinerary_line_takes_tomorrow(lab: Lab) -> None:
    await lab.say("criar viagem Bali")
    out = await lab.say("roteiro amanhã 10:00 museu")
    assert "museu" in out and (today_local() + timedelta(days=1)).strftime("%d/%m") in out


@db
@pytest.mark.bug("B-057")
async def test_a_service_price_at_the_end_is_the_amount_not_the_name(lab: Lab) -> None:
    out = await lab.say("guardar serviço eletricista 90 euros")
    assert "eletricista 90" not in out
    assert "90,00" in out


@db
@pytest.mark.bug("A-39")
async def test_a_task_with_a_day_gets_it_as_its_due_date(lab: Lab) -> None:
    out = await lab.say("tarefa: pagar o dentista amanhã às 14h")
    assert "*pagar o dentista*" in out
    due = await lab.scalar(select(Task.due_date).where(Task.member_id == lab.member_id))
    assert due == today_local() + timedelta(days=1)


@db
@pytest.mark.bug("A-63")
async def test_the_task_list_shows_the_due_date_like_the_confirmation(lab: Lab) -> None:
    await lab.say("tarefa: enviar relatório até sexta")
    out = await lab.say("minhas tarefas")
    assert "prazo: 20" not in out and "/" in out


@db
@pytest.mark.bug("B-058")
async def test_deleting_a_goal_by_its_full_name_does_not_ask_which(lab: Lab) -> None:
    await lab.say("meta: correr 10 km")
    await lab.say("meta: correr 10 km em novembro")
    out = await lab.say("apaga a meta correr 10 km")
    assert "correr 10 km" in out and "mais de uma" not in out


@db
@pytest.mark.bug("A-63")
async def test_a_comment_about_a_task_is_not_a_new_task(lab: Lab) -> None:
    await lab.say("tarefa difícil essa")
    assert await _count(lab, Task) == 0
    await lab.say("tarefa: difícil essa")  # with a colon it is what the member asked for
    assert await _count(lab, Task) == 1
