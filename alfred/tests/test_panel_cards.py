# ruff: noqa: E501
"""The Resumo and Dinheiro cards through the HTTP API: numbers, phrases, empty states, parity
between cards and list, IDOR, hostile input and the 300 ms budget."""

from __future__ import annotations

import json
import os
import statistics
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import delete, text

from alfred import panel_phrases as pp
from alfred import panel_tokens
from alfred.db import AsyncSessionLocal
from alfred.models import Base, Budget, Expense, Household, Iou, Member, RecurringItem
from alfred.web_security import _dashboard_limiter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    _dashboard_limiter._hits.clear()
    yield
    _dashboard_limiter._hits.clear()


@pytest.fixture
def today(monkeypatch):
    """Freeze the panel's "today" (the API reads it through ``alfred.panel_api.today_local``)."""

    def _set(day: date = TODAY) -> None:
        monkeypatch.setattr("alfred.panel_api.today_local", lambda: day)

    _set()
    return _set


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        return str(m.dashboard_token)


def _at(d: date, hour: int = 12) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=UTC)


def _exp(lab, amount, merchant, cat, day, *, kind="expense", status="paid", when=None):
    return Expense(
        member_id=lab.member_id, household_id=lab.household_id, transaction_type=kind,
        amount=amount, merchant=merchant, category=cat, status=status,
        expense_date=when or _at(day),
    )  # fmt: skip


async def _mockup_data(lab, *, secret: str = "x") -> None:
    """Joao's October 2026 (until the 14th) and September, shaped like the approved mockups."""
    o = lambda d: date(2026, 10, d)  # noqa: E731
    s = lambda d: date(2026, 9, d)  # noqa: E731
    rows = [
        _exp(lab, 3840, "Salário", "inkomen", o(1), kind="income", status="received"),
        _exp(lab, 1150, "Aluguel", "wonen", o(1)),
        _exp(lab, 94.40, "Albert Heijn", "supermarkt", o(1)),
        _exp(lab, 120, "Jumbo", "supermarkt", o(5)),
        _exp(lab, 97.60, "Albert Heijn", "supermarkt", o(9)),
        _exp(lab, 64, "Foodhallen", "restaurant", o(3)),
        _exp(lab, 50, "Pizzeria", "restaurant", o(6)),
        _exp(lab, 34, f"Sushi {secret}", "restaurant", o(10)),
        _exp(lab, 60, "Cinema", "entertainment", o(4)),
        _exp(lab, 34, "Bowling", "entertainment", o(11)),
        _exp(lab, 18.40, "Uber", "transport", o(1)),
        _exp(lab, 22.60, "Uber", "transport", o(7)),
        _exp(lab, 20, "OV", "transport", o(12)),
        _exp(lab, 38, "Apotheek", "gezondheid", o(8)),
        _exp(lab, 118, "Energia", "wonen", o(18), status="to_pay"),
        # September: the same elapsed days (1..14) are the comparison; the 20th must not count
        _exp(lab, 3840, "Salário", "inkomen", s(1), kind="income", status="received"),
        _exp(lab, 1150, "Aluguel", "wonen", s(1)),
        _exp(lab, 100, "Albert Heijn", "supermarkt", s(2)),
        _exp(lab, 90, "Jumbo", "supermarkt", s(6)),
        _exp(lab, 99, "Albert Heijn", "supermarkt", s(11)),
        _exp(lab, 40, "Foodhallen", "restaurant", s(3)),
        _exp(lab, 30, "Pizzeria", "restaurant", s(8)),
        _exp(lab, 26, "Sushi", "restaurant", s(12)),
        _exp(lab, 105, "Cinema", "entertainment", s(5)),
        _exp(lab, 94, "Uber", "transport", s(4)),
        _exp(lab, 38, "Apotheek", "gezondheid", s(9)),
        _exp(lab, 500, "Móveis", "overig", s(20)),
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="restaurant", monthly_limit=120),
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="supermarkt", monthly_limit=400),
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="transport", monthly_limit=150),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Aluguel", amount=1150, category="wonen", due_day=1, next_due_date=date(2026, 11, 1)),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Energia", amount=118, category="wonen", due_day=18, next_due_date=o(18)),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Internet", amount=42, category="wonen", due_day=20, next_due_date=o(20)),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Seguro de saúde", amount=138, category="gezondheid", due_day=25, next_due_date=o(25)),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Notebook", amount=100, kind="installment", installments_total=13, installments_paid=0, due_day=28, next_due_date=o(28)),
        Iou(member_id=lab.member_id, person=f"Marta {secret}", amount=34.5, direction="owed_to_me", note="jantar", created_at=datetime(2026, 10, 5, 12, tzinfo=UTC)),
    ]  # fmt: skip
    await lab.add(*rows)


def _card(body: dict, card_id: str) -> dict:
    return next(c for c in body["cards"] if c["id"] == card_id)


async def _get(client, token, tab, query="") -> dict:
    r = await client.get(f"/api/d/{token}/{tab}{query}")
    assert r.status_code == 200, (tab, query, r.text[:200])
    assert r.headers["cache-control"] == "no-store"
    return r.json()


# ── Resumo ────────────────────────────────────────────────────────────────────


@db
async def test_summary_has_the_cards_in_order_with_stable_ids(lab, client, today) -> None:
    await _mockup_data(lab)
    body = await _get(client, await _token(lab.member_id), "summary")
    assert [c["id"] for c in body["cards"]] == [
        "balance",
        "upcoming",
        "categories",
        "budgets",
        "blue_days",
        "owed",
    ]
    assert body["today"] == "2026-10-14" and body["filter"]["start"] == "2026-10-01"
    for c in body["cards"]:
        assert isinstance(c["empty"], bool) and "values" in c and "phrase" in c
        assert c["phrase"] is None or set(c["phrase"]) == {"key", "text", "chat", "severity"}


@db
async def test_balance_compare_and_projection_by_components(lab, client, today) -> None:
    await _mockup_data(lab)
    b = _card(await _get(client, await _token(lab.member_id), "summary"), "balance")
    income, expense = (
        3840.0,
        1150 + 94.40 + 120 + 97.60 + 64 + 50 + 34 + 60 + 34 + 18.40 + 22.60 + 20 + 38,
    )
    assert b["values"] == {
        "income": income,
        "expense": round(expense, 2),
        "balance": round(income - expense, 2),
    }
    # against the same days (1..14) of September, never the whole month
    assert b["compare"]["start"] == "2026-09-01" and b["compare"]["end"] == "2026-09-15"
    sept = 1150 + 100 + 90 + 99 + 40 + 30 + 26 + 105 + 94 + 38  # 1772
    assert b["compare"]["expense"]["previous"] == sept
    assert b["compare"]["income"] == {"previous": 3840.0, "delta": 0.0, "pct": 0.0}
    p = b["projection"]
    # components computed by hand: bills Energia (the pending entry) + Internet + Seguro + Notebook
    committed = 118 + 42 + 138 + 100
    variable_30d = (
        expense - 1150 + 500
    )  # the rent is a fixed item; the 500 of 20 Sep is inside the window
    assert p["available"] and p["sufficient"] and p["committed"] == committed
    assert p["days_left"] == 17 and p["window_days"] == 30 and p["month_end"] == "2026-10-31"
    assert p["variable"] == pytest.approx(variable_30d / 30 * 17, abs=0.02)
    assert p["projected"] == pytest.approx(
        income - expense - committed - variable_30d / 30 * 17, abs=0.03
    )
    assert p["low"] < p["projected"] < p["high"]
    assert b["phrase"]["key"] == "balance_projection" and "estimativa" in b["phrase"]["text"]
    past = _card(
        await _get(client, await _token(lab.member_id), "summary", "?month=2026-09"), "balance"
    )
    assert past["projection"] == {"available": False, "reason": "not_current_month"}
    assert past["phrase"] is None


@db
async def test_projection_is_withheld_with_thin_data_and_outside_the_running_month(
    lab, client, today
) -> None:
    await lab.add(*[_exp(lab, 10, f"m{i}", "overig", date(2026, 10, 10 + i)) for i in range(3)])
    token = await _token(lab.member_id)
    b = _card(await _get(client, token, "summary"), "balance")
    p = b["projection"]
    assert (
        p["available"] and p["sufficient"] is False and p["projected"] is None and p["low"] is None
    )
    assert p["history_days"] == 5 and p["entries"] == 3
    assert (
        b["phrase"]["key"] == "projection_insufficient"
        and b["phrase"]["chat"] == '"gastei 25 no mercado"'
    )
    for query, reason in [
        ("?categories=overig", "filtered"),
        ("?kind=expense", "filtered"),
        ("?state=paid", "filtered"),
    ]:
        card = _card(await _get(client, token, "summary", query), "balance")
        assert card["projection"] == {"available": False, "reason": reason}, query
        assert card["phrase"] is None


@db
async def test_categories_card_numbers_trend_and_phrase(lab, client, today) -> None:
    await _mockup_data(lab)
    c = _card(await _get(client, await _token(lab.member_id), "summary"), "categories")
    assert [i["category"] for i in c["items"]] == [
        "wonen",
        "supermarkt",
        "restaurant",
        "entertainment",
        "transport",
    ]
    by = {i["category"]: i for i in c["items"]}
    assert by["supermarkt"]["amount"] == 312.0 and by["supermarkt"]["previous"] == 289.0
    assert by["supermarkt"]["delta"] == 23.0 and by["supermarkt"]["trend"] == "up"
    assert by["restaurant"]["delta"] == 52.0 and by["restaurant"]["delta_pct"] == 54.2
    assert by["transport"]["trend"] == "down" and by["transport"]["delta"] == -33.0
    assert by["wonen"]["trend"] == "flat" and by["wonen"]["delta"] == 0.0
    assert c["others"] == {"amount": 38.0, "count": 1}  # gezondheid beyond the top 5
    assert c["values"]["total"] == 1803.0 and c["values"]["previous_total"] == 1772.0
    assert (
        c["phrase"]["text"]
        == "Restaurante é o que mais cresceu: € 52,00 a mais do que em setembro."
    )
    assert c["phrase"]["chat"] == '"mês contra mês"'
    assert not any(
        i["category"] == "overig" for i in c["items"]
    )  # the 500 of 20 Sep is outside the window


@db
async def test_budgets_card(lab, client, today) -> None:
    await _mockup_data(lab)
    c = _card(await _get(client, await _token(lab.member_id), "summary"), "budgets")
    assert [(i["category"], i["spent"], i["limit"], i["pct"], i["level"]) for i in c["items"]] == [
        ("restaurant", 148.0, 120.0, 123, 100),
        ("supermarkt", 312.0, 400.0, 78, 0),
        ("transport", 61.0, 150.0, 41, 0),
    ]
    assert c["items"][0]["crossed_on"] == "2026-10-10" and c["items"][1]["days_to_80"] == 1
    assert c["values"] == {
        "spent": 521.0,
        "limit": 670.0,
        "pct": 78,
        "month": "2026-10",
        "day": 14,
        "days_in_month": 31,
    }
    assert c["phrase"]["key"] == "budget_over" and c["phrase"]["severity"] == "attention"
    assert c["phrase"]["text"] == "Restaurante passou do orçamento: € 148,00 de € 120,00 (123%)."
    only = _card(
        await _get(client, await _token(lab.member_id), "summary", "?categories=transport"),
        "budgets",
    )
    assert [i["category"] for i in only["items"]] == ["transport"] and only["phrase"] is None


@db
async def test_budget_pace_phrase_when_nothing_is_over(lab, client, today) -> None:
    await lab.add(
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="supermarkt", monthly_limit=400),
        *[_exp(lab, 104, f"AH{i}", "supermarkt", date(2026, 10, 2 + 3 * i)) for i in range(3)],
    )  # fmt: skip
    c = _card(await _get(client, await _token(lab.member_id), "summary"), "budgets")
    assert c["items"][0]["pct"] == 78 and c["phrase"]["key"] == "budget_pace"
    assert "1 dia" in c["phrase"]["text"]


@db
async def test_upcoming_card_items_and_phrase(lab, client, today) -> None:
    await _mockup_data(lab)
    c = _card(await _get(client, await _token(lab.member_id), "summary"), "upcoming")
    assert [(i["name"], i["amount"], i["due"], i["days"], i["source"]) for i in c["items"]] == [
        ("Energia", 118.0, "2026-10-18", 4, "expense"),  # the pending entry replaces the fixed item
        ("Internet", 42.0, "2026-10-20", 6, "recurring"),
        ("Seguro de saúde", 138.0, "2026-10-25", 11, "recurring"),
        ("Notebook", 100.0, "2026-10-28", 14, "recurring"),
        ("Aluguel", 1150.0, "2026-11-01", 18, "recurring"),  # inside the 30-day horizon
    ]
    assert c["items"][0]["due_text"] == "vence em 4 dias" and c["items"][0]["direction"] == "pay"
    assert c["values"] == {
        "to_pay": 1548.0,
        "to_receive": 0.0,
        "overdue": 0.0,
        "count_pay": 5,
        "count_receive": 0,
        "count_overdue": 0,
        "horizon_days": 30,
    }
    assert c["phrase"]["key"] == "upcoming_ok" and c["phrase"]["chat"] == '"paguei Energia"'
    assert "5 contas" in c["phrase"]["text"] and "€ 1.548,00" in c["phrase"]["text"]
    # a category filter keeps only the fixed items of that category
    ge = _card(
        await _get(client, await _token(lab.member_id), "summary", "?categories=gezondheid"),
        "upcoming",
    )
    assert [i["name"] for i in ge["items"]] == ["Seguro de saúde"]


@db
async def test_upcoming_overdue_is_flagged_as_attention_and_receivables_are_listed(
    lab, client, today
) -> None:
    await lab.add(
        _exp(lab, 80, "Água", "wonen", date(2026, 10, 9), status="to_pay"),
        _exp(
            lab, 300, "Cliente", "inkomen", date(2026, 10, 20), kind="income", status="to_receive"
        ),
    )
    c = _card(await _get(client, await _token(lab.member_id), "summary"), "upcoming")
    assert c["values"]["overdue"] == 80.0 and c["values"]["to_receive"] == 300.0
    assert c["items"][0]["overdue"] is True and c["items"][0]["due_text"] == "atrasada há 5 dias"
    assert c["phrase"]["severity"] == "attention" and c["phrase"]["chat"] == '"paguei Água"'


@db
async def test_blue_days_and_owed_cards(lab, client, today) -> None:
    await _mockup_data(lab, secret="alfa")
    body = await _get(client, await _token(lab.member_id), "summary")
    blue = _card(body, "blue_days")
    assert (
        blue["values"]["blue"] == 14
        and blue["values"]["elapsed"] == 14
        and blue["values"]["longest"] == 14
    )
    assert blue["phrase"]["text"] == "14 de 14 dias no azul; a maior sequência foi de 14 dias."
    owed = _card(body, "owed")
    assert owed["values"] == {"total": 34.5, "count": 1}
    assert owed["items"] == [
        {"person": "Marta alfa", "amount": 34.5, "note": "jantar", "since": "2026-10-05", "days": 9}
    ]
    assert (
        owed["phrase"]["chat"] == '"Marta alfa pagou 34,50"' and "9 dias" in owed["phrase"]["text"]
    )


# ── Dinheiro ──────────────────────────────────────────────────────────────────


@db
async def test_money_has_the_cards_in_order(lab, client, today) -> None:
    await _mockup_data(lab)
    body = await _get(client, await _token(lab.member_id), "money")
    assert [c["id"] for c in body["cards"]] == [
        "transactions", "top_expenses", "month_vs_month", "fixed_variable", "daily",
        "categories", "avg_ticket", "recurring", "owed",
    ]  # fmt: skip


@db
async def test_transactions_card_groups_by_day_with_the_whole_day_sum(lab, client, today) -> None:
    await _mockup_data(lab)
    c = _card(await _get(client, await _token(lab.member_id), "money"), "transactions")
    assert c["page"] == {"number": 1, "size": 50, "pages": 1, "total": 15}
    assert [d["date"] for d in c["days"]] == sorted((d["date"] for d in c["days"]), reverse=True)
    first = next(d for d in c["days"] if d["date"] == "2026-10-01")
    assert (
        first["income"] == 3840.0
        and first["expense"] == 1262.8
        and first["net"] == 2577.2
        and first["entries_total"] == 4
    )
    pending_day = next(d for d in c["days"] if d["date"] == "2026-10-18")
    assert pending_day["expense"] == 0.0  # a pending entry is listed but not in the day sum
    assert (
        pending_day["entries"][0]["status"] == "to_pay"
        and pending_day["entries"][0]["settled"] is False
    )
    assert (
        c["phrase"]["text"] == "15 lançamentos em outubro. O maior gasto foi Aluguel, € 1.150,00."
    )


@db
async def test_transactions_pages_cover_the_list_exactly_once(lab, client, today) -> None:
    await lab.add(
        *[_exp(lab, 1 + i % 7, f"m{i}", "overig", date(2026, 10, 1 + i % 13)) for i in range(120)]
    )
    token = await _token(lab.member_id)
    seen = 0
    for n in (1, 2, 3, 4):
        c = _card(await _get(client, token, "money", f"?page={n}"), "transactions")
        got = sum(len(d["entries"]) for d in c["days"])
        assert c["page"]["pages"] == 3 and c["page"]["total"] == 120 and c["page"]["number"] == n
        assert got == (50, 50, 20, 0)[n - 1]
        seen += got
    assert seen == 120


@db
async def test_top_expenses_month_vs_month_fixed_variable_daily_ticket_recurring(
    lab, client, today
) -> None:
    await _mockup_data(lab)
    body = await _get(client, await _token(lab.member_id), "money")
    top = _card(body, "top_expenses")
    assert [(i["merchant"], i["amount"]) for i in top["items"]][:2] == [
        ("Aluguel", 1150.0),
        ("Jumbo", 120.0),
    ]
    assert top["items"][0]["pct_of_total"] == 64 and top["items"][0]["pct_of_top"] == 100
    assert top["phrase"]["text"] == "Aluguel é 64% de tudo que saiu."

    mom = _card(body, "month_vs_month")
    assert mom["values"]["expense"] == {
        "current": 1803.0,
        "previous": 1772.0,
        "delta": 31.0,
        "pct": 1.7,
    }
    assert mom["values"]["income"] == {
        "current": 3840.0,
        "previous": 3840.0,
        "delta": 0.0,
        "pct": 0.0,
    }
    assert mom["drivers"][0] == {"category": "restaurant", "label": "Restaurante", "delta": 52.0}
    assert mom["phrase"] is None or mom["phrase"]["key"] == "mom_driver"

    fv = _card(body, "fixed_variable")
    assert fv["values"] == {"fixed": 1150.0, "variable": 653.0, "total": 1803.0, "fixed_pct": 64}
    assert fv["phrase"]["key"] == "fixed_variable"

    daily = _card(body, "daily")
    assert daily["unit"] == "day" and len(daily["points"]) == 14
    assert daily["values"]["max_date"] == "2026-10-01" and daily["values"]["max"] == 1262.8
    assert daily["values"]["elapsed_days"] == 14 and daily["values"]["quiet_days"] == 3
    assert daily["phrase"]["key"] == "busiest_day" and "01/10" in daily["phrase"]["text"]

    tk = _card(body, "avg_ticket")
    assert tk["values"]["count"] == 13 and tk["values"]["average"] == round(1803 / 13, 2)
    assert tk["values"]["previous_average"] == round(1772 / 10, 2) and tk["phrase"] is None

    rec = _card(body, "recurring")
    assert (
        rec["values"]["count"] == 5
        and rec["values"]["monthly_total"] == 1150 + 118 + 42 + 138 + 100
    )
    nb = next(i for i in rec["items"] if i["name"] == "Notebook")
    assert nb["installment"] == {"number": 1, "total": 13} and nb["due_text"] == "vence em 14 dias"
    assert next(i for i in rec["items"] if i["name"] == "Aluguel")["installment"] is None


@db
async def test_long_ranges_fold_the_daily_series_into_weeks(lab, client, today) -> None:
    await lab.add(
        _exp(lab, 10, "a", "overig", date(2026, 3, 4)),
        _exp(lab, 20, "b", "overig", date(2026, 10, 2)),
    )
    token = await _token(lab.member_id)
    d = _card(await _get(client, token, "money", "?from=2026-01-01&to=2026-10-14"), "daily")
    assert d["unit"] == "week" and len(d["points"]) < 60
    assert sum(p["amount"] for p in d["points"]) == 30.0 and d["phrase"] is None


# ── empty states ──────────────────────────────────────────────────────────────


@db
@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
async def test_empty_member_gets_empty_cards_that_teach_the_chat_in_their_language(
    lab, client, today, lang
) -> None:
    async with AsyncSessionLocal() as s:
        (await s.get(Member, lab.member_id)).language = lang
        await s.commit()
    token = await _token(lab.member_id)
    for tab in ("summary", "money"):
        body = await _get(client, token, tab)
        assert body["lang"] == lang
        for c in body["cards"]:
            assert c["empty"] is True, (tab, c["id"])
            assert c["phrase"] is None
            h = c["hint"]
            assert (
                h["chat"].startswith('"')
                and h["chat"].endswith('"')
                and "{" not in h["text"] + h["chat"]
            )
            assert h["text"] in (pp.PHRASES[h["key"]][lang],)
            assert (
                h["chat"]
                == pp.CHAT[
                    {
                        "empty_ledger": "log_expense",
                        "empty_budgets": "set_budget",
                        "empty_upcoming": "add_recurring",
                        "empty_recurring": "add_recurring",
                        "empty_owed": "add_owed",
                    }[h["key"]]
                ][lang]
            )


@db
async def test_phrases_come_in_the_members_language(lab, client, today) -> None:
    await _mockup_data(lab)
    async with AsyncSessionLocal() as s:
        (await s.get(Member, lab.member_id)).language = "nl"
        await s.commit()
    body = await _get(client, await _token(lab.member_id), "summary")
    cats = _card(body, "categories")
    assert (
        cats["items"][2]["label"] == "Restaurant"
        and "meer dan in september" in cats["phrase"]["text"]
    )
    assert _card(body, "upcoming")["items"][0]["due_text"] == "vervalt over 4 dagen"
    assert _card(body, "budgets")["phrase"]["text"].startswith("Restaurant zit boven het budget")


# ── one filter, one total: cards and list ────────────────────────────────────

COMBOS = [
    "", "?kind=expense", "?kind=income", "?categories=supermarkt", "?categories=supermarkt,restaurant",
    "?state=paid", "?state=to_pay,paid", "?state=received,to_receive",
    "?kind=expense&categories=restaurant&state=paid", "?from=2026-10-02&to=2026-10-09",
    "?from=2026-09-20&to=2026-10-12&kind=expense", "?month=2026-09", "?month=2026-08",
]  # fmt: skip


@db
@pytest.mark.parametrize("query", COMBOS)
async def test_every_card_and_the_list_agree_on_the_total(lab, client, today, query) -> None:
    await _mockup_data(lab)
    await lab.add(
        *[
            _exp(
                lab,
                1.5 + i % 9,
                f"x{i}",
                ("supermarkt", "restaurant", "overig")[i % 3],
                date(2026, 10, 1 + i % 13),
                status=("paid", "to_pay")[i % 2],
            )
            for i in range(70)
        ]
    )
    token = await _token(lab.member_id)
    money = await _get(client, token, "money", query)
    summary = await _get(client, token, "summary", query)
    first = _card(money, "transactions")
    listed = []
    for n in range(1, first["page"]["pages"] + 1):
        c = _card(
            await _get(client, token, "money", f"{query}{'&' if query else '?'}page={n}"),
            "transactions",
        )
        listed += [e for d in c["days"] for e in d["entries"]]
    assert len(listed) == first["page"]["total"]
    list_exp = round(sum(e["amount"] for e in listed if e["kind"] == "expense" and e["settled"]), 2)
    list_inc = round(sum(e["amount"] for e in listed if e["kind"] == "income" and e["settled"]), 2)
    day_net = (
        round(sum(d["net"] for d in first["days"]), 2) if first["page"]["pages"] == 1 else None
    )
    bal = _card(summary, "balance")["values"]
    assert first["values"]["expense"] == list_exp == bal["expense"]
    assert first["values"]["income"] == list_inc == bal["income"]
    assert _card(money, "categories")["values"]["total"] == list_exp
    assert _card(summary, "categories")["values"]["total"] == list_exp
    assert _card(money, "top_expenses")["values"]["total"] == list_exp
    assert _card(money, "fixed_variable")["values"]["total"] == list_exp
    assert _card(money, "month_vs_month")["values"]["expense"]["current"] == list_exp
    assert _card(money, "month_vs_month")["values"]["income"]["current"] == list_inc
    assert round(_card(money, "daily")["values"]["total"], 2) == list_exp
    avg = _card(money, "avg_ticket")["values"]
    assert avg["total"] == list_exp
    if day_net is not None:
        assert day_net == round(list_inc - list_exp, 2)
    if list_exp:
        cats = _card(money, "categories")
        assert (
            round(
                sum(i["amount"] for i in cats["items"])
                + (cats["others"] or {"amount": 0})["amount"],
                2,
            )
            == list_exp
        )


# ── security: IDOR, hostile input, XSS ───────────────────────────────────────


async def _make_member():
    async with AsyncSessionLocal() as s:
        hh = Household(name="other")
        s.add(hh)
        await s.flush()
        other = Member(
            household_id=hh.id,
            wa_phone="3160" + uuid.uuid4().hex[:7],
            consent_state="accepted",
            language="pt",
        )
        s.add(other)
        await s.commit()
        return other.id, hh.id


async def _drop_member(member_id, household_id) -> None:
    async with AsyncSessionLocal() as s:
        for table in reversed(Base.metadata.sorted_tables):
            if "member_id" in table.c and table.name != "member":
                await s.execute(delete(table).where(table.c.member_id == member_id))
        await s.execute(delete(Member).where(Member.id == member_id))
        await s.execute(delete(Household).where(Household.id == household_id))
        await s.commit()


@db
async def test_another_members_token_never_sees_my_cards(lab, client, today) -> None:
    secret = "SEGREDO" + uuid.uuid4().hex[:6]
    await _mockup_data(lab, secret=secret)
    mine = await _token(lab.member_id)
    other_id, hh_id = await _make_member()
    try:
        theirs = await _token(other_id)
        for tab, query in [
            ("summary", ""),
            ("money", ""),
            ("money", "?page=1"),
            ("summary", "?month=2026-09"),
            ("money", "?categories=restaurant"),
        ]:
            body = await client.get(f"/api/d/{theirs}/{tab}{query}")
            assert body.status_code == 200
            raw = body.text
            assert secret not in raw and "Albert" not in raw and "Notebook" not in raw
            for c in body.json()["cards"]:
                assert c["empty"] is True, (tab, query, c["id"])
        assert secret in (await client.get(f"/api/d/{mine}/money")).text  # the data is really there
        assert secret in (await client.get(f"/api/d/{mine}/summary")).text
        # a token for another member, with my ids smuggled in the query, still shows nothing of mine
        trick = await client.get(
            f"/api/d/{theirs}/money?member_id={lab.member_id}&trip={uuid.uuid4()}"
        )
        assert secret not in trick.text
    finally:
        await _drop_member(other_id, hh_id)


HOSTILE = [
    "?from=0001-01-01&to=9999-12-31", "?from=9999-12-31&to=9999-12-31", "?from=2026-02-29&to=2026-03-01",
    "?from=2026-10-14&to=2026-10-01", "?month=2026-02-30", "?month=9999-99", "?month=0000-00", "?month=2026-13",
    "?month=%00", "?month=2026-1", "?from=&to=", "?page=-1", "?page=0", "?page=abc", "?page=1e3", "?page=%D9%A3",
    "?page=99999999999999999999", "?page=1&page=2", "?categories=<script>alert(1)</script>", "?categories=" + ",".join(f"c{i}" for i in range(500)),
    "?kind=income%00", "?state=paid;drop table expense", "?trip=../../etc/passwd", "?trip=" + "a" * 5000,
    "?from=2026-10-01T00:00:00&to=2026-10-31", "?from=２０２６-10-01&to=2026-10-31", "?month=2026-10&from=2026-01-01&to=2026-12-31",
]  # fmt: skip


@db
@pytest.mark.parametrize("query", HOSTILE)
async def test_hostile_query_strings_never_break_the_cards(lab, client, today, query) -> None:
    await _mockup_data(lab)
    token = await _token(lab.member_id)
    for tab in ("summary", "money"):
        r = await client.get(f"/api/d/{token}/{tab}{query}")
        assert r.status_code == 200, (tab, query, r.status_code)
        body = r.json()
        assert len(body["cards"]) >= 6 and body["filter"]["start"] < body["filter"]["end"]
        for c in body["cards"]:
            assert "id" in c and "empty" in c and "phrase" in c


@db
@pytest.mark.parametrize(
    ("day", "prev_window"),
    [
        (
            date(2026, 11, 1),
            ("2026-10-01", "2026-10-02"),
        ),  # the first day of a month: one day of October
        (date(2027, 1, 1), ("2026-12-01", "2026-12-02")),  # year turn
        (date(2028, 2, 29), ("2028-01-01", "2028-01-30")),  # leap day
        (date(2026, 3, 29), ("2026-02-01", "2026-03-01")),  # 29 Mar against a 28-day February
        (date(2026, 10, 31), ("2026-09-01", "2026-10-01")),  # 31 against a 30-day September
    ],
)
async def test_calendar_edges_of_today_on_every_card(lab, client, today, day, prev_window) -> None:
    today(day)
    await lab.add(
        _exp(lab, 20, "a", "overig", day),
        _exp(lab, 5, "b", "overig", day - timedelta(days=40)),
        Budget(member_id=lab.member_id, household_id=lab.household_id, category="overig", monthly_limit=50),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name="Luz", amount=9, next_due_date=day + timedelta(days=3)),
    )  # fmt: skip
    token = await _token(lab.member_id)
    for tab in ("summary", "money"):
        body = await _get(client, token, tab)
        assert body["filter"]["start"] == day.replace(day=1).isoformat()
        assert body["today"] == day.isoformat()
    b = _card(await _get(client, token, "summary"), "balance")
    assert (b["compare"] or {"start": prev_window[0], "end": prev_window[1]})[
        "start"
    ] == prev_window[0]
    assert _card(await _get(client, token, "summary"), "categories")["compare"] == {
        "start": prev_window[0],
        "end": prev_window[1],
    }
    assert _card(await _get(client, token, "summary"), "upcoming")["items"][0]["days"] == 3


@db
async def test_an_entry_at_half_past_midnight_belongs_to_the_new_local_day(
    lab, client, today
) -> None:
    today(date(2026, 11, 1))
    # 23:30 UTC on 31 Oct is 00:30 on 1 Nov in Amsterdam
    await lab.add(
        _exp(
            lab,
            12,
            "Bar",
            "restaurant",
            date(2026, 10, 31),
            when=datetime(2026, 10, 31, 23, 30, tzinfo=UTC),
        )
    )
    token = await _token(lab.member_id)
    body = await _get(client, token, "money")
    t = _card(body, "transactions")
    assert t["days"][0]["date"] == "2026-11-01" and t["values"]["expense"] == 12.0
    prev = _card(await _get(client, token, "money", "?month=2026-10"), "transactions")
    assert prev["values"]["expense"] == 0.0


@db
async def test_hostile_text_in_cards_is_returned_as_inert_json(lab, client, today) -> None:
    evil = '<script>window.__x=1</script>"><img src=x onerror=window.__x=1>'
    await lab.add(
        _exp(lab, 50, evil, "restaurant", date(2026, 10, 3)),
        Iou(member_id=lab.member_id, person=evil[:55], amount=10, direction="owed_to_me", created_at=datetime(2026, 10, 1, tzinfo=UTC)),
        RecurringItem(member_id=lab.member_id, household_id=lab.household_id, name=evil[:100], amount=5, next_due_date=date(2026, 10, 20)),
    )  # fmt: skip
    token = await _token(lab.member_id)
    for tab in ("summary", "money"):
        r = await client.get(f"/api/d/{token}/{tab}")
        assert r.headers["content-type"].startswith("application/json")
        assert (
            r.headers["cache-control"] == "no-store"
            and r.headers["x-content-type-options"] == "nosniff"
        )
        json.loads(r.text)
    owed = _card(await _get(client, token, "summary"), "owed")
    assert (
        owed["phrase"] is None or owed["phrase"]["chat"].count('"') == 2
    )  # quotes in a name cannot escape the command


# ── performance ──────────────────────────────────────────────────────────────


@db
async def test_tab_endpoints_stay_under_300ms_with_20k_rows(lab, client, today) -> None:
    await _mockup_data(lab)
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                """
                INSERT INTO expense (id, member_id, household_id, transaction_type, amount, currency,
                                     merchant, category, expense_date, created_at, status)
                SELECT gen_random_uuid(), :m, :h, 'expense', (g % 90) + 1.25, 'EUR', 'm' || (g % 200),
                       (ARRAY['supermarkt','restaurant','transport','overig','wonen','entertainment'])[g % 6 + 1],
                       timestamptz '2026-07-01 12:00+00' + (g % 105) * interval '1 day' + (g % 600) * interval '1 minute',
                       now(), CASE WHEN g % 40 = 0 THEN 'to_pay' ELSE 'paid' END
                FROM generate_series(1, 20000) g
                """
            ),
            {"m": lab.member_id, "h": lab.household_id},
        )
        await s.commit()
    token = await _token(lab.member_id)
    for tab in ("summary", "money"):
        await client.get(f"/api/d/{token}/{tab}?month=2026-10")  # warm the pool
        for query in (
            "?month=2026-10",
            "?from=2026-07-01&to=2026-10-14",
            "?month=2026-10&categories=supermarkt,restaurant&state=paid",
        ):
            times = []
            for _ in range(3):
                t0 = time.perf_counter()
                r = await client.get(f"/api/d/{token}/{tab}{query}")
                times.append(time.perf_counter() - t0)
                assert r.status_code == 200
            assert statistics.median(times) < 0.3, (tab, query, times)
