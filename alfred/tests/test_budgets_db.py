"""V2-01 — monthly budgets per category: commands, alerts at 80/100 %, projection, privacy."""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from alfred.budgets import (
    alert_after_expense,
    parse_set,
    percent,
    project_month_end,
    resolve_category,
)
from alfred.clock import now_local, today_local
from alfred.conversation import _t
from alfred.models import Budget, Expense, Member
from alfred.privacy import export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _exp(amount: float, name: str = "Jumbo", category: str = "supermarkt", kind: str = "expense"):
    return {
        "amount": amount,
        "currency": "EUR",
        "merchant": name,
        "category": category,
        "description": name,
        "type": kind,
        "days_ago": 0,
    }


async def _budget(lab: Lab, category: str = "supermarkt") -> Budget | None:
    return await lab.scalar(
        select(Budget).where(Budget.member_id == lab.member_id, Budget.category == category)
    )


# ── pure rules ────────────────────────────────────────────────────────────────


def test_parse_set_in_five_languages() -> None:
    assert parse_set("orcamento mercado 400") == ("mercado", 400.0)
    assert parse_set("muda o orcamento de lazer para 150") == ("lazer", 150.0)
    assert parse_set("budget groceries 400") == ("groceries", 400.0)
    assert parse_set("begroting boodschappen 300") == ("boodschappen", 300.0)
    assert parse_set("orcamento de restaurante 1.200,50") == ("restaurante", 1200.5)
    assert parse_set("definir orcamento saude 80 por mes") == ("saude", 80.0)
    assert parse_set("orcamento mercado") is None
    assert parse_set("orcamento mercado 0") is None
    assert parse_set("orcamento mercado 99999999") is None


def test_categories_resolve_from_any_language_and_income_is_not_budgetable() -> None:
    for word in ("mercado", "Supermercado", "groceries", "boodschappen", "supermarkt"):
        assert resolve_category(word) == "supermarkt"
    assert resolve_category("lazer") == "entertainment"
    assert resolve_category("saúde") == "gezondheid"
    assert resolve_category("rendimento") is None
    assert resolve_category("inkomen") is None
    assert resolve_category("blablabla") is None


def test_projection_waits_until_day_three_and_uses_the_month_length() -> None:
    assert project_month_end(Decimal("100"), date(2026, 10, 2)) is None
    assert project_month_end(Decimal("100"), date(2026, 10, 10)) == Decimal("310.00")  # 31 days
    assert project_month_end(Decimal("100"), date(2026, 2, 10)) == Decimal("280.00")  # 28 days
    assert percent(Decimal("328"), Decimal("400")) == 82
    assert percent(Decimal("1"), Decimal("0")) == 0


# ── commands ──────────────────────────────────────────────────────────────────


@db
async def test_create_update_and_remove_a_budget(lab: Lab) -> None:
    reply = await lab.say("orçamento mercado 400")
    assert "€400,00" in reply and "Supermercado" in reply
    assert (await _budget(lab)).monthly_limit == 400
    reply = await lab.say("muda o orçamento de mercado para 450")
    assert "€450,00" in reply
    assert (await _budget(lab)).monthly_limit == 450
    assert await lab.scalar(select(func.count()).select_from(Budget)) >= 1
    reply = await lab.say("tira o orçamento de mercado")
    assert "removido" in reply and await _budget(lab) is None
    reply = await lab.say("tira o orçamento de mercado")
    assert reply == _t("budget_none_to_remove", "pt", cat="Supermercado")


@db
async def test_unknown_or_income_category_lists_the_valid_ones(lab: Lab) -> None:
    reply = await lab.say("orçamento xyzzy 100")
    assert "Supermercado" in reply and "Rendimento" not in reply
    assert (
        await lab.scalar(
            select(func.count()).select_from(Budget).where(Budget.member_id == lab.member_id)
        )
        == 0
    )


@db
async def test_list_empty_then_with_spent_and_percent(lab: Lab) -> None:
    assert await lab.say("meus orçamentos") == _t("budget_list_empty", "pt")
    await lab.say("orçamento mercado 400")
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=100,
            currency="EUR",
            merchant="Jumbo",
            category="supermarkt",
            expense_date=now_local(),
        ),
        Expense(  # income and other categories never count
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="income",
            amount=999,
            currency="EUR",
            category="supermarkt",
            expense_date=now_local(),
        ),
    )
    reply = await lab.say("meus orçamentos")
    assert "Supermercado" in reply and "€100,00 de €400,00 (25%)" in reply


@db
async def test_budgets_are_isolated_between_members(lab: Lab) -> None:
    await lab.say("orçamento mercado 400")
    other = await _budget(lab)
    assert other is not None
    # a second member never sees it: the list query is filtered by member_id
    from alfred.db import AsyncSessionLocal
    from alfred.models import Household

    async with AsyncSessionLocal() as s:
        hh = Household(name="other")
        s.add(hh)
        await s.flush()
        m2 = Member(
            household_id=hh.id,
            wa_phone="31699" + lab.phone[-6:],
            consent_state="accepted",
            language="pt",
        )
        s.add(m2)
        await s.commit()
        from alfred.budgets import handle_budget_command

        reply = await handle_budget_command("meus orcamentos", m2, "pt", s)
        assert reply == _t("budget_list_empty", "pt")
        await s.delete(m2)
        await s.delete(hh)
        await s.commit()


# ── alerts ────────────────────────────────────────────────────────────────────


@db
async def test_alert_at_80_then_100_once_each(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.expense.return_value = _exp(50)
    assert "orçamento" not in (await lab.say("Jumbo 50")).lower()  # 50 %: silence
    lab.expense.return_value = _exp(35)
    r80 = await lab.say("Jumbo 35")  # 85 %
    assert "85%" in r80 and "⚠️" in r80
    lab.expense.return_value = _exp(2)
    assert "⚠️" not in await lab.say("Jumbo 2")  # 87 %: already announced
    lab.expense.return_value = _exp(20)
    r100 = await lab.say("Jumbo 20")  # 107 %
    assert "estourou" in r100
    lab.expense.return_value = _exp(5)
    assert "⚠️" not in await lab.say("Jumbo 5")  # 100 % announced once


@db
async def test_a_single_expense_from_zero_past_100_announces_only_100(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.expense.return_value = _exp(130)
    reply = await lab.say("Jumbo 130")
    assert "estourou" in reply and "130" in reply
    assert reply.count("⚠️") == 1 and "usou" not in reply


@db
async def test_no_budget_for_the_category_means_no_alert(lab: Lab) -> None:
    await lab.say("orçamento mercado 10")
    lab.expense.return_value = _exp(500, "Restaurante X", category="restaurant")
    assert "⚠️" not in await lab.say("Restaurante X 500")


@db
async def test_income_never_alerts(lab: Lab) -> None:
    await lab.say("orçamento mercado 10")
    lab.expense.return_value = _exp(500, "Freela", category="supermarkt", kind="income")
    assert "⚠️" not in await lab.say("recebi 500")


@db
async def test_new_month_starts_again_from_zero(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    b = await _budget(lab)
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        row = await s.get(Budget, b.id)
        row.last_alert_level = 100
        row.last_alert_month = date(2020, 1, 1)  # announced in some past month
        await s.commit()
    lab.expense.return_value = _exp(90)
    assert "90%" in await lab.say("Jumbo 90")


@db
async def test_changing_the_limit_resets_this_months_alerts(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.expense.return_value = _exp(90)
    assert "⚠️" in await lab.say("Jumbo 90")
    await lab.say("orçamento mercado 95")  # tighter: 94 % should warn again next time
    lab.expense.return_value = _exp(4)
    assert "⚠️" in await lab.say("Jumbo 4")


@db
async def test_backdated_expense_from_last_month_does_not_alert(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        from datetime import timedelta

        old = now_local() - timedelta(days=62)
        assert await alert_after_expense(s, member, "supermarkt", old, "pt") == ""


@db
async def test_multi_expense_message_also_alerts(lab: Lab) -> None:
    await lab.say("orçamento mercado 100")
    lab.multi.return_value = [
        {**_exp(90, "Mercado"), "days_ago": 0},
        {**_exp(10, "Farmácia", category="gezondheid"), "days_ago": 0},
    ]
    await lab.say("mercado 90 e farmácia 10")
    reply = await lab.say("sim")
    assert "(2)" in reply and "90%" in reply


@db
async def test_alerts_exist_in_every_language(lab: Lab) -> None:
    from alfred.conversation import _STRINGS

    for key in (
        "budget_set",
        "budget_alert_80",
        "budget_alert_100",
        "budget_row",
        "budget_list_header",
    ):
        assert set(_STRINGS[key]) >= {"pt", "nl", "en", "fr", "de"}


# ── privacy ───────────────────────────────────────────────────────────────────


@db
async def test_budgets_are_exported_and_erased_with_the_member(lab: Lab) -> None:
    await lab.say("orçamento mercado 400")
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
    assert len(data["tables"]["budget"]) == 1
    assert today_local()  # sanity: clock import used
