"""V2-14 — analysis by validated spec: no SQL from the model, limits, saved views, isolation."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from alfred.analysis import (
    DAILY_LIMIT,
    Spec,
    _like,
    add_months,
    format_result,
    looks_like_analysis,
    run,
    windows,
)
from alfred.clock import local_tz, today_local
from alfred.db import AsyncSessionLocal
from alfred.models import Expense, LlmUsage, Member, SavedView
from alfred.privacy import export_member_data
from alfred.validation import sanitize_analysis_spec
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 15)


# ── the spec is the only thing the model controls ─────────────────────────────


def test_valid_spec_gets_defaults() -> None:
    s = sanitize_analysis_spec({})
    assert s == {
        "metric": "spent",
        "group_by": "none",
        "period": "this_month",
        "compare_to": "none",
        "category": None,
        "merchant": None,
        "top_n": 5,
    }
    full = sanitize_analysis_spec(
        {
            "metric": "Net",
            "group_by": "category",
            "period": "last_3_months",
            "compare_to": "previous_period",
            "filters": {"category": "Restaurant", "merchant": "  Jumbo  "},
            "top_n": 99,
        }
    )
    assert full["metric"] == "net" and full["category"] == "restaurant"
    assert full["merchant"] == "Jumbo" and full["top_n"] == 10


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "spent",
        [],
        {"metric": "sum(amount); drop table expense"},
        {"group_by": "member_id"},
        {"period": "next_year"},
        {"compare_to": "yesterday"},
        {"filters": {"category": "food"}},
        {"filters": ["restaurant"]},
        {"top_n": "many"},
    ],
)
def test_invalid_spec_is_rejected_not_coerced(bad) -> None:
    assert sanitize_analysis_spec(bad) is None


def test_month_series_ignores_compare() -> None:
    s = sanitize_analysis_spec({"group_by": "month", "compare_to": "previous_period"})
    assert s["compare_to"] == "none"


def test_like_escapes_wildcards() -> None:
    assert _like("50%_off\\") == "%50\\%\\_off\\\\%"


def test_gate_keeps_ordinary_messages_away_from_the_llm() -> None:
    yes = [
        "quanto gastei com restaurante nos últimos 3 meses comparado ao trimestre anterior?",
        "gastos por categoria este ano vs ano passado",
        "hoeveel heb ik dit jaar uitgegeven per categorie",
        "how much did I spend this year, by category?",
        "combien ai-je dépensé par catégorie cette annee",
        "wie viel habe ich dieses jahr ausgegeben nach kategorie",
    ]
    no = ["oi", "gastei 45 no jumbo", "quanto gastei?", "bom dia, tudo bem com você hoje?"]
    from alfred.parsing import strip_accents

    assert all(looks_like_analysis(strip_accents(t.lower())) for t in yes)
    assert not any(looks_like_analysis(strip_accents(t.lower())) for t in no)


# ── periods ───────────────────────────────────────────────────────────────────


def test_this_month_is_compared_with_the_same_days_of_last_month() -> None:
    cur, prev = windows(Spec(period="this_month", compare_to="previous_period"), TODAY)
    assert (cur.start, cur.end) == (date(2026, 10, 1), date(2026, 10, 16))
    assert (prev.start, prev.end) == (date(2026, 9, 1), date(2026, 9, 16))


def test_short_previous_month_is_capped() -> None:
    today = date(2026, 3, 31)
    _, prev = windows(Spec(period="this_month", compare_to="previous_period"), today)
    assert (prev.start, prev.end) == (date(2026, 2, 1), date(2026, 3, 1))  # Feb has 28 days


def test_last_three_months_are_full_months_and_compare_to_the_three_before() -> None:
    cur, prev = windows(Spec(period="last_3_months", compare_to="previous_period"), TODAY)
    assert (cur.start, cur.end) == (date(2026, 7, 1), date(2026, 10, 1))
    assert (prev.start, prev.end) == (date(2026, 4, 1), date(2026, 7, 1))


def test_this_year_vs_same_period_last_year() -> None:
    cur, prev = windows(Spec(period="this_year", compare_to="same_period_last_year"), TODAY)
    assert (cur.start, cur.end) == (date(2026, 1, 1), date(2026, 10, 16))
    assert (prev.start, prev.end) == (date(2025, 1, 1), date(2025, 10, 16))


def test_last_days_compare_with_the_days_right_before() -> None:
    cur, prev = windows(Spec(period="last_7_days", compare_to="previous_period"), TODAY)
    assert (cur.start, cur.end) == (date(2026, 10, 9), date(2026, 10, 16))
    assert (prev.start, prev.end) == (date(2026, 10, 2), date(2026, 10, 9))


def test_add_months_clamps_the_day() -> None:
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)


# ── queries (real DB) ─────────────────────────────────────────────────────────


def _at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=local_tz())


def _exp(
    lab: Lab, day: date, amount: float, category="restaurant", merchant="Jumbo", kind="expense"
):
    return Expense(
        id=uuid.uuid4(),
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        merchant=merchant,
        category=category,
        expense_date=_at(day),
    )


def _months() -> tuple[date, date]:
    """Fixed dates for the direct queries: ``run`` gets ``TODAY``, never the wall clock."""
    return date(2026, 10, 1), date(2026, 9, 10)


def _prev_month_10th() -> date:
    """For the conversation tests (which run on the real clock): always inside last month."""
    return add_months(today_local().replace(day=1), -1).replace(day=10)


async def _run(lab: Lab, spec: Spec):
    async with AsyncSessionLocal() as s:
        return await run(s, lab.member_id, spec, TODAY)


@db
async def test_total_by_category_with_comparison(lab: Lab) -> None:
    m0, last = _months()
    await lab.add(
        _exp(lab, m0, 30, "restaurant"),
        _exp(lab, m0, 70, "supermarkt"),
        _exp(lab, last, 20, "restaurant"),
        _exp(lab, last, 10, "supermarkt"),
        _exp(lab, m0, 1000, "inkomen", kind="income"),
    )
    res = await _run(
        lab, Spec(group_by="category", period="this_month", compare_to="previous_period")
    )
    assert res.total == Decimal("100.00")  # income never counts as spending
    assert res.rows == [("supermarkt", Decimal("70.00")), ("restaurant", Decimal("30.00"))]
    assert res.prev_total == Decimal("30.00")
    assert res.prev_by_key == {"restaurant": Decimal("20.00"), "supermarkt": Decimal("10.00")}


@db
async def test_other_metrics(lab: Lab) -> None:
    m0, _ = _months()
    await lab.add(
        _exp(lab, m0, 30), _exp(lab, m0, 10), _exp(lab, m0, 500, "inkomen", kind="income")
    )
    assert (await _run(lab, Spec(metric="income"))).total == Decimal("500.00")
    assert (await _run(lab, Spec(metric="net"))).total == Decimal("460.00")
    assert (await _run(lab, Spec(metric="count"))).total == Decimal("2.00")
    assert (await _run(lab, Spec(metric="average"))).total == Decimal("20.00")


@db
async def test_month_series_and_top_n(lab: Lab) -> None:
    m0, last = _months()
    await lab.add(
        _exp(lab, m0, 5, merchant="A"),
        _exp(lab, m0, 9, merchant="B"),
        _exp(lab, m0, 7, merchant="C"),
        _exp(lab, last, 4, merchant="A"),
    )
    series = await _run(lab, Spec(group_by="month", period="last_6_months"))
    assert [k for k, _ in series.rows] == [f"{last:%Y-%m}"]
    top = await _run(lab, Spec(group_by="merchant", period="this_month", top_n=2))
    assert [k for k, _ in top.rows] == ["b", "c"] and top.total == Decimal("21.00")


@db
async def test_merchant_filter_is_a_literal_not_a_pattern_or_sql(lab: Lab) -> None:
    m0, _ = _months()
    await lab.add(_exp(lab, m0, 10, merchant="Jumbo"), _exp(lab, m0, 20, merchant="100% Bio_shop"))
    assert (await _run(lab, Spec(merchant="%"))).total == Decimal("20.00")  # only the literal %
    assert (await _run(lab, Spec(merchant="bio_s"))).total == Decimal("20.00")
    assert (await _run(lab, Spec(merchant="x_s"))).total == Decimal("0.00")  # _ is not a wildcard
    evil = "'; DROP TABLE expense; --"
    assert (await _run(lab, Spec(merchant=evil))).total == Decimal("0.00")
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 2
    )


@db
async def test_members_are_isolated(lab: Lab) -> None:
    m0, _ = _months()
    await lab.add(_exp(lab, m0, 10))
    other = uuid.uuid4()
    async with AsyncSessionLocal() as s:
        res = await run(s, other, Spec(), TODAY)
    assert res.total == Decimal("0.00")


@db
@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
async def test_card_in_five_languages(lab: Lab, lang: str) -> None:
    m0, last = _months()
    await lab.add(_exp(lab, m0, 30), _exp(lab, last, 20))
    spec = Spec(group_by="category", period="this_month", compare_to="previous_period")
    text = format_result(spec, await _run(lab, spec), lang)
    assert "*€30,00*" in text and "€20,00" in text and "+50%" in text
    assert "{" not in text  # no unfilled placeholder
    empty = format_result(Spec(period="last_year"), await _run(lab, Spec(period="last_year")), lang)
    assert "{" not in empty


# ── conversation: question → card, limit, saved views ─────────────────────────

QUESTION = "quanto gastei com restaurante nos últimos 3 meses comparado ao trimestre anterior?"
SPEC = {
    "supported": True,
    "spec": {
        "metric": "spent",
        "period": "last_3_months",
        "compare_to": "previous_period",
        "filters": {"category": "restaurant"},
    },
}


def _llm(value):
    return patch("alfred.analysis.extract_analysis_spec", AsyncMock(return_value=value))


@db
async def test_question_returns_a_card_and_offers_to_save(lab: Lab) -> None:
    await lab.add(_exp(lab, _prev_month_10th(), 40))
    with _llm(SPEC) as mock:
        reply = await lab.say(QUESTION)
    mock.assert_awaited_once()
    assert "Gastos" in reply and "€40,00" in reply and "salva essa visão como" in reply


@db
async def test_unsupported_and_invalid_specs_get_an_honest_answer(lab: Lab) -> None:
    for raw in ({"supported": False}, {"supported": True, "spec": {"period": "next_year"}}):
        with _llm(raw):
            reply = await lab.say(QUESTION)
        assert "Ainda não consigo responder isso" in reply


@db
async def test_without_a_model_the_message_falls_through(lab: Lab) -> None:
    with _llm(None):
        reply = await lab.say(QUESTION)
    assert reply == "[llm]"  # normal reply path, nothing invented


@db
async def test_ordinary_messages_never_call_the_analysis_model(lab: Lab) -> None:
    with _llm(SPEC) as mock:
        await lab.say("bom dia, tudo bem com você hoje?")
    mock.assert_not_awaited()


@db
async def test_daily_limit(lab: Lab) -> None:
    await lab.add(
        *[
            LlmUsage(member_id=lab.member_id, purpose="analysis", model="m")
            for _ in range(DAILY_LIMIT)
        ]
    )
    with _llm(SPEC) as mock:
        reply = await lab.say(QUESTION)
    mock.assert_not_awaited()
    assert str(DAILY_LIMIT) in reply and "limite diário" in reply
    await lab.add(
        LlmUsage(member_id=lab.member_id, purpose="classify", model="m")
    )  # other purposes don't count


@db
async def test_other_purposes_do_not_use_the_analysis_allowance(lab: Lab) -> None:
    await lab.add(
        *[
            LlmUsage(member_id=lab.member_id, purpose="classify", model="m")
            for _ in range(DAILY_LIMIT)
        ]
    )
    with _llm(SPEC) as mock:
        await lab.say(QUESTION)
    mock.assert_awaited_once()


@db
async def test_save_list_run_delete_a_view(lab: Lab) -> None:
    assert "Não tenho nenhuma análise recente" in await lab.say(
        "salva essa visão como 'restaurantes'"
    )
    assert "ainda não tem visões" in await lab.say("minhas visões")
    await lab.add(_exp(lab, _prev_month_10th(), 40))
    with _llm(SPEC):
        await lab.say(QUESTION)
    assert "Visão 'restaurantes' salva" in await lab.say("salva essa visão como 'restaurantes'")
    assert "• restaurantes" in await lab.say("minhas visões")
    with _llm(SPEC) as mock:
        card = await lab.say("roda 'restaurantes'")
    mock.assert_not_awaited()  # running a saved view costs no model call
    assert "€40,00" in card
    with _llm(SPEC):
        await lab.say(QUESTION)  # a new analysis to save, under a name already in use
    assert "Já existe" in await lab.say("salva essa visão como Restaurantes")
    assert "apagada" in await lab.say("apaga a visão restaurantes")
    assert "ainda não tem visões" in await lab.say("minhas visões")
    assert "Não achei a visão" in await lab.say("roda a visão restaurantes")


@db
async def test_generic_run_phrases_are_not_claimed(lab: Lab) -> None:
    assert await lab.say("roda uma volta no parque") == "[llm]"


@db
async def test_only_the_latest_draft_is_kept(lab: Lab) -> None:
    with _llm(SPEC):
        await lab.say(QUESTION)
        await lab.say(QUESTION)
    rows = await lab.rows(
        select(SavedView).where(SavedView.member_id == lab.member_id, SavedView.name.is_(None))
    )
    assert len(rows) == 1


@db
async def test_view_limit(lab: Lab) -> None:
    from alfred.analysis import MAX_VIEWS

    await lab.add(
        *[SavedView(member_id=lab.member_id, name=f"v{i}", spec={}) for i in range(MAX_VIEWS)]
    )
    with _llm(SPEC):
        await lab.say(QUESTION)
    assert str(MAX_VIEWS) in await lab.say("salva essa visão como outra")


@db
async def test_views_are_private_and_exported(lab: Lab) -> None:
    await lab.add(SavedView(member_id=lab.member_id, name="minha", spec=dict(SPEC["spec"])))
    other_phone = "3161" + uuid.uuid4().hex[:7]
    async with AsyncSessionLocal() as s:
        other = Member(
            household_id=lab.household_id,
            wa_phone=other_phone,
            consent_state="accepted",
            language="pt",
        )
        s.add(other)
        await s.commit()
        other_id = other.id
    try:
        from alfred.analysis import handle_view_command

        async with AsyncSessionLocal() as s:
            m = await s.get(Member, other_id)
            assert "ainda não tem visões" in await handle_view_command("minhas visoes", m, "pt", s)
            mine = await s.get(Member, lab.member_id)
            assert "• minha" in await handle_view_command("minhas visoes", mine, "pt", s)
            data = await export_member_data(s, mine)
        assert [r["name"] for r in data["tables"]["saved_view"]] == ["minha"]
    finally:
        async with AsyncSessionLocal() as s:
            await s.delete(await s.get(Member, other_id))
            await s.commit()


@db
async def test_spec_is_never_sql_in_the_stored_view(lab: Lab) -> None:
    with _llm(
        {
            "supported": True,
            "spec": {**SPEC["spec"], "filters": {"merchant": "x'; drop table expense; --"}},
        }
    ):
        await lab.say(QUESTION)
    (row,) = await lab.rows(select(SavedView.spec).where(SavedView.member_id == lab.member_id))
    assert set(row[0]) == {
        "metric",
        "group_by",
        "period",
        "compare_to",
        "category",
        "merchant",
        "top_n",
    }
    assert (
        await lab.scalar(select(func.count()).select_from(Expense)) is not None
    )  # table still there
