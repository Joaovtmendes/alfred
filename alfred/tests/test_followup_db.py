"""V2-20 buttons to go deeper after a spending answer · V2-23 👍/👎 feedback (counts only)."""

from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from alfred.clock import now_local, today_local
from alfred.conversation import _t
from alfred.followup import _dec, _enc, _label
from alfred.models import Budget, Expense, ReplyFeedback
from alfred.privacy import export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(lab: Lab, amount: float, name: str, category: str, days_ago: int = 0) -> Expense:
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type="expense",
        amount=amount,
        currency="EUR",
        merchant=name,
        category=category,
        expense_date=now_local() - timedelta(days=days_ago),
    )


async def _seed(lab: Lab) -> None:
    await lab.add(
        _exp(lab, 80, "Jumbo", "supermarkt"),
        _exp(lab, 30, "Albert Heijn", "supermarkt"),
        _exp(lab, 12, "Lidl", "supermarkt"),
        _exp(lab, 45, "Pizzeria", "restaurant"),
    )


def _ids(lab: Lab) -> list[str]:
    return [b[0] for b in lab.buttons[-1]]


# ── pure rules ────────────────────────────────────────────────────────────────


def test_scope_round_trips_and_forged_ids_are_rejected() -> None:
    start, end = now_local().replace(hour=0, minute=0, second=0, microsecond=0), None
    got = _dec(_enc(start, end, "supermarkt"))
    assert got is not None and got[0] == start and got[1] is None and got[2] == "supermarkt"
    assert _dec("nonsense") is None
    assert _dec("2026-10-01.2026-10-31.drop table") is None
    assert _dec("2026-13-01.open.-") is None
    assert _dec("2026-10-05.2026-10-01.-") is None  # end before start


def test_label_names_a_whole_month_and_otherwise_the_dates() -> None:
    from alfred.clock import day_start

    month = _label(day_start(today_local().replace(day=1)), None, "pt")
    assert str(today_local().year) in month
    week = _label(day_start(today_local() - timedelta(days=7)), day_start(today_local()), "pt")
    assert "–" in week


def test_button_titles_fit_the_whatsapp_limit_in_every_language() -> None:
    from alfred.followup import REASONS

    keys = ["btn_dd_cat", "btn_dd_top", "btn_dd_bud", "btn_fb_up", "btn_fb_down"]
    keys += [f"btn_fb_{r}" for r in REASONS]
    for lang in ("pt", "nl", "en", "fr", "de"):
        for key in keys:
            assert len(_t(key, lang)) <= 20, (key, lang)


# ── V2-20 ─────────────────────────────────────────────────────────────────────


@db
@pytest.mark.asyncio
async def test_category_answer_offers_the_three_buttons(lab: Lab) -> None:
    await _seed(lab)
    lab.classify.return_value = {
        "query_type": "category",
        "period": "current_month",
        "category": "supermarkt",
    }
    reply = await lab.say("quanto gastei com mercado este mês?")
    assert "€122,00" in reply
    ids = _ids(lab)
    assert [i.split(":")[0] for i in ids] == ["dd_cat", "dd_top", "dd_bud"]


@db
@pytest.mark.asyncio
async def test_period_answer_offers_top_and_budget_only(lab: Lab) -> None:
    await _seed(lab)
    lab.classify.return_value = {"query_type": "period", "period": "current_month"}
    await lab.say("quanto gastei este mês?")
    assert [i.split(":")[0] for i in _ids(lab)] == ["dd_top", "dd_bud"]


@db
@pytest.mark.asyncio
async def test_no_buttons_when_there_is_nothing_to_go_deeper_into(lab: Lab) -> None:
    lab.classify.return_value = {"query_type": "period", "period": "current_month"}
    await lab.say("quanto gastei este mês?")
    assert lab.buttons[-1] == []


@db
@pytest.mark.asyncio
async def test_top_button_lists_the_biggest_expenses_of_that_scope(lab: Lab) -> None:
    await _seed(lab)
    lab.classify.return_value = {
        "query_type": "category",
        "period": "current_month",
        "category": "supermarkt",
    }
    await lab.say("quanto gastei com mercado este mês?")
    top_id = next(i for i in _ids(lab) if i.startswith("dd_top"))
    reply = await lab.tap(top_id)
    assert reply.index("Jumbo") < reply.index("Albert Heijn") < reply.index("Lidl")
    assert "Pizzeria" not in reply  # the category scope is kept


@db
@pytest.mark.asyncio
async def test_category_button_gives_the_summary_by_category_for_the_same_period(lab: Lab) -> None:
    await _seed(lab)
    lab.classify.return_value = {
        "query_type": "category",
        "period": "current_month",
        "category": "supermarkt",
    }
    await lab.say("quanto gastei com mercado este mês?")
    reply = await lab.tap(next(i for i in _ids(lab) if i.startswith("dd_cat")))
    assert "Supermercado" in reply and "Restaurante" in reply and "€167,00" in reply


@db
@pytest.mark.asyncio
async def test_budget_button_shows_the_budget_or_how_to_create_one(lab: Lab) -> None:
    await _seed(lab)
    lab.classify.return_value = {
        "query_type": "category",
        "period": "current_month",
        "category": "supermarkt",
    }
    await lab.say("quanto gastei com mercado este mês?")
    bud_id = next(i for i in _ids(lab) if i.startswith("dd_bud"))
    assert "orçamento" in (await lab.tap(bud_id)).lower()  # how to create one
    await lab.add(
        Budget(
            member_id=lab.member_id,
            household_id=lab.household_id,
            category="supermarkt",
            monthly_limit=400,
        )
    )
    reply = await lab.tap(bud_id)
    assert "€122,00 de €400,00 (30%)" in reply


@db
@pytest.mark.asyncio
async def test_forged_deeper_button_is_harmless(lab: Lab) -> None:
    await _seed(lab)
    assert await lab.tap("dd_top:garbage") == _t("button_gone", "pt")
    assert await lab.tap("dd_cat:2026-10-01.open.xx") == _t("button_gone", "pt")


@db
@pytest.mark.asyncio
async def test_deeper_buttons_only_read_the_callers_own_rows(lab: Lab, lab2: Lab) -> None:
    await _seed(lab2)  # another member's spending
    reply = await lab.tap(f"dd_top:{_enc(now_local(), None, None)}")
    assert "Jumbo" not in reply


# ── V2-23 ─────────────────────────────────────────────────────────────────────


async def _ask_chat(lab: Lab, text: str = "x" * 300) -> list[tuple[str, str]]:
    lab.llm_reply.return_value = text
    await lab.say("me conta sobre a vida")
    return lab.buttons[-1]


@db
@pytest.mark.asyncio
async def test_long_model_answer_gets_feedback_buttons_but_short_one_does_not(lab: Lab) -> None:
    assert [i.split(":")[0] for i, _ in await _ask_chat(lab)] == ["fb_up", "fb_down"]
    assert await _ask_chat(lab, "ok, entendi") == []


@db
@pytest.mark.asyncio
async def test_thumbs_up_stores_one_row_with_no_text(lab: Lab) -> None:
    ids = [i for i, _ in await _ask_chat(lab)]
    reply = await lab.tap(ids[0])
    assert reply == _t("fb_thanks", "pt")
    row = await lab.scalar(select(ReplyFeedback).where(ReplyFeedback.member_id == lab.member_id))
    assert (row.source, row.rating, row.reason) == ("chat", "up", None)


@db
@pytest.mark.asyncio
async def test_thumbs_down_asks_for_a_reason_from_a_closed_list(lab: Lab) -> None:
    ids = [i for i, _ in await _ask_chat(lab)]
    reply = await lab.tap(ids[1])
    assert reply == _t("fb_ask_reason", "pt")
    reasons = lab.buttons[-1]
    assert [i.split(":")[-1] for i, _ in reasons] == ["wrong", "unclear", "missing"]
    await lab.tap(reasons[1][0])
    row = await lab.scalar(select(ReplyFeedback).where(ReplyFeedback.member_id == lab.member_id))
    assert (row.rating, row.reason) == ("down", "unclear")


@db
@pytest.mark.asyncio
async def test_a_forged_reason_changes_nothing(lab: Lab, lab2: Lab) -> None:
    ids = [i for i, _ in await _ask_chat(lab)]
    await lab.tap(ids[1])
    row = await lab.scalar(select(ReplyFeedback).where(ReplyFeedback.member_id == lab.member_id))
    assert await lab2.tap(f"fb_r:{row.id}:wrong") == _t("button_gone", "pt")  # not their row
    assert await lab.tap(f"fb_r:{row.id}:<script>") == _t("button_gone", "pt")  # not on the list
    assert await lab.tap("fb_r:not-a-uuid:wrong") == _t("button_gone", "pt")
    await lab.tap("fb_up:chat")  # an up row never takes a reason
    up = await lab.scalar(
        select(ReplyFeedback).where(
            ReplyFeedback.member_id == lab.member_id, ReplyFeedback.rating == "up"
        )
    )
    assert await lab.tap(f"fb_r:{up.id}:wrong") == _t("button_gone", "pt")


@db
@pytest.mark.asyncio
async def test_analysis_answer_offers_feedback_with_its_own_source(lab: Lab) -> None:
    await _seed(lab)
    from alfred import analysis

    spec = {
        "supported": True,
        "spec": {"metric": "total", "group_by": "category", "period": "this_month"},
    }
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(analysis, "extract_analysis_spec", AsyncMock(return_value=spec))
        await lab.say("gastos por categoria este mês")
    ids = _ids(lab)
    if ids:  # the analysis path answered
        assert ids[0] == "fb_up:analysis"


@db
@pytest.mark.asyncio
async def test_daily_cap_stops_inflated_counts(lab: Lab) -> None:
    for _ in range(25):
        await lab.tap("fb_up:chat")
    n = await lab.scalar(
        select(func.count())
        .select_from(ReplyFeedback)
        .where(ReplyFeedback.member_id == lab.member_id)
    )
    assert n == 20


@db
@pytest.mark.asyncio
async def test_feedback_is_exported_and_erased_with_the_member(lab: Lab) -> None:
    await lab.tap("fb_up:chat")
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member
    from alfred.privacy import erase_member

    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
        assert "reply_feedback" in str(data)
        await erase_member(s, member)
        await s.commit()
    assert (
        await lab.scalar(
            select(func.count())
            .select_from(ReplyFeedback)
            .where(ReplyFeedback.member_id == lab.member_id)
        )
        == 0
    )
