"""V2-16 — entry states: paid / to pay / received / to receive, and who sees them."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import select, text

from alfred.clock import local_tz, today_local
from alfred.db import AsyncSessionLocal
from alfred.insights import blue_days
from alfred.ledger_status import (
    MAX_PENDING,
    NewPending,
    parse_pending,
    parse_settle,
    pending_summary,
)
from alfred.models import Expense
from alfred.privacy import export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 15)


def _at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=local_tz())


def _row(lab: Lab, merchant, amount, status, kind="expense", day=None, category="overig"):
    return Expense(
        id=uuid.uuid4(),
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        merchant=merchant,
        category=category,
        status=status,
        expense_date=_at(day or today_local()),
    )


async def _exps(lab: Lab) -> list[Expense]:
    async with AsyncSessionLocal() as s:
        return list((await s.execute(select(Expense))).scalars().all())


# ── parsing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "kind", "name", "amount"),
    [
        ("a pagar luz 120 dia 20", "to_pay", "luz", 120),
        ("te betalen stroom 80", "to_pay", "stroom", 80),
        ("to pay electricity 45,50", "to_pay", "electricity", 45.5),
        ("à payer loyer 900", "to_pay", "loyer", 900),
        ("zu zahlen strom 60", "to_pay", "strom", 60),
        ("a receber freela 300", "to_receive", "freela", 300),
        ("te ontvangen huur 500", "to_receive", "huur", 500),
        ("to receive refund 25", "to_receive", "refund", 25),
    ],
)
def test_parse_pending_in_five_languages(body, kind, name, amount) -> None:
    r = parse_pending(body, body.replace("à", "a"), TODAY)
    assert isinstance(r, NewPending)
    assert (r.kind, float(r.amount)) == (kind, float(amount))
    assert name in r.name.lower()


def test_parse_pending_due_date_and_errors() -> None:
    r = parse_pending("a pagar luz 120 dia 20", "a pagar luz 120 dia 20", TODAY)
    assert r.due.day == 20 and r.due.month == 10
    assert parse_pending("a pagar luz", "a pagar luz", TODAY) == "bad_amount"
    assert parse_pending("a pagar 120", "a pagar 120", TODAY) == "no_name"
    assert parse_pending("comprei pão 3", "comprei pao 3", TODAY) is None


def test_parse_settle() -> None:
    assert parse_settle("paguei a luz") is not None
    assert parse_settle("recebi o freela") is not None
    assert parse_settle("bom dia") is None


# ── lifecycle ─────────────────────────────────────────────────────────────────


@db
async def test_create_list_and_settle_a_bill(lab: Lab) -> None:
    reply = await lab.say("a pagar luz 120 dia 20")
    assert "120" in reply
    (e,) = await _exps(lab)
    assert e.status == "to_pay" and e.transaction_type == "expense"
    assert "luz" in (await lab.say("o que tenho a pagar")).lower()
    reply = await lab.say("paguei a luz")
    assert "120" in reply
    (e,) = await _exps(lab)
    assert e.status == "paid"
    assert "nada a pagar" in (await lab.say("o que tenho a pagar")).lower()


@db
async def test_receivable_becomes_received_income(lab: Lab) -> None:
    await lab.say("a receber freela 300")
    (e,) = await _exps(lab)
    assert (e.status, e.transaction_type) == ("to_receive", "income")
    await lab.say("recebi o freela")
    (e,) = await _exps(lab)
    assert e.status == "received"


@db
async def test_paid_amount_overrides_the_stored_one(lab: Lab) -> None:
    await lab.say("a pagar luz 120")
    await lab.say("paguei a luz 110")
    (e,) = await _exps(lab)
    assert (e.status, float(e.amount)) == ("paid", 110.0)


@db
async def test_ambiguous_settle_asks_which(lab: Lab) -> None:
    await lab.add(
        _row(lab, "luz casa", 50, "to_pay"),
        _row(lab, "luz garagem", 20, "to_pay"),
    )
    reply = await lab.say("paguei a luz")
    assert "luz casa" in reply and "luz garagem" in reply
    rows = await _exps(lab)
    assert all(r.status == "to_pay" for r in rows)


@db
async def test_unmatched_paguei_still_reaches_the_other_handlers(lab: Lab) -> None:
    await lab.say("aluguel 1200 todo dia 1")
    reply = await lab.say("paguei o aluguel")
    assert "aluguel" in reply.lower()
    rows = await _exps(lab)
    assert rows and all(r.status == "paid" for r in rows)


@db
async def test_limit_of_pending_items(lab: Lab) -> None:
    await lab.add(*[_row(lab, f"c{i}", 1, "to_pay") for i in range(MAX_PENDING)])
    reply = await lab.say("a pagar extra 10")
    assert str(MAX_PENDING) in reply
    assert len(await _exps(lab)) == MAX_PENDING


# ── reports only see settled entries ──────────────────────────────────────────


async def _seed(lab: Lab) -> None:
    await lab.add(
        _row(lab, "super", 40, "paid", category="boodschappen"),
        _row(lab, "salário", 1000, "received", kind="income", category="inkomen"),
        _row(lab, "luz", 500, "to_pay", category="wonen"),
        _row(lab, "freela", 700, "to_receive", kind="income", category="inkomen"),
    )


@db
async def test_balance_ignores_pending_but_shows_forecast(lab: Lab) -> None:
    await _seed(lab)
    reply = await lab.say("saldo")
    assert "1.000,00" in reply and "40,00" in reply
    assert "500,00" in reply and "700,00" in reply  # forecast line
    assert "960,00" in reply  # 1000 - 40


@db
async def test_summary_top_and_comparison_ignore_pending(lab: Lab) -> None:
    await _seed(lab)
    summary = await lab.say("resumo do mês")
    assert "500,00" not in summary and "700,00" not in summary
    top = await lab.say("maiores gastos do mês")
    assert "500,00" not in top
    cmp_reply = await lab.say("compara este mês com o mês passado")
    assert "500,00" not in cmp_reply


@db
async def test_budget_and_analysis_ignore_pending(lab: Lab) -> None:
    from alfred.analysis import Spec, run

    await _seed(lab)
    async with AsyncSessionLocal() as s:
        res = await run(
            s,
            lab.member_id,
            Spec(metric="spent"),
            today_local(),
        )
    assert float(res.total) == 40.0


@db
async def test_blue_days_ignore_pending(lab: Lab) -> None:
    day = today_local()
    await lab.add(_row(lab, "luz", 5000, "to_pay", day=day))
    async with AsyncSessionLocal() as s:
        assert await blue_days(s, lab.member_id, day) is None


@db
async def test_pending_summary_and_overdue(lab: Lab) -> None:
    today = today_local()
    await lab.add(
        _row(lab, "late", 30, "to_pay", day=today - timedelta(days=5)),
        _row(lab, "soon", 20, "to_pay", day=today + timedelta(days=3)),
        _row(lab, "owed", 10, "to_receive", kind="income"),
    )
    async with AsyncSessionLocal() as s:
        p = await pending_summary(s, lab.member_id, today)
    assert (float(p.to_pay), float(p.to_receive), p.overdue_count) == (50.0, 10.0, 1)
    assert float(p.overdue_total) == 30.0


@db
async def test_monthly_summary_warns_about_overdue(lab: Lab) -> None:
    from alfred.monthly_summary import build_summary

    today = today_local()
    await lab.add(
        _row(lab, "super", 40, "paid", category="boodschappen"),
        _row(lab, "late", 30, "to_pay", day=today - timedelta(days=3)),
    )
    async with AsyncSessionLocal() as s:
        from alfred.models import Member

        member = await s.get(Member, lab.member_id)
        out = await build_summary(s, member, "pt", today.replace(day=1))
    assert out and "em atraso" in out


@db
async def test_export_contains_the_status(lab: Lab) -> None:
    await lab.add(_row(lab, "luz", 5, "to_pay"))
    async with AsyncSessionLocal() as s:
        from alfred.models import Member

        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
    assert "to_pay" in str(data)


# ── schema ────────────────────────────────────────────────────────────────────


@db
async def test_default_status_follows_the_transaction_type(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        a = Expense(
            id=uuid.uuid4(),
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="income",
            amount=1,
            expense_date=_at(today_local()),
        )
        b = Expense(
            id=uuid.uuid4(),
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=1,
            expense_date=_at(today_local()),
        )
        s.add_all([a, b])
        await s.commit()
        assert (a.status, b.status) == ("received", "paid")


@db
async def test_migration_backfill_sql_marks_old_income_received(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        s.add(_row(lab, "old", 9, "paid", kind="income"))
        await s.commit()
        await s.execute(
            text(
                "UPDATE expense SET status='received' "
                "WHERE transaction_type='income' AND status='paid'"
            )
        )
        await s.commit()
    (e,) = await _exps(lab)
    assert e.status == "received"
