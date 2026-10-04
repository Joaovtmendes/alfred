# ruff: noqa: E501
"""V2-12 — accounting through the router and the panel: modes, tags, BTW, tab cards, CSV export."""

from __future__ import annotations

import os
import re
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal
from alfred.models import AuditLog, Expense, Member
from alfred.privacy import export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.panel_api.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.conversation.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.settings.settings.base_url", "https://alfred.test")


def _e(lab: Lab, amount, *, month=10, day=5, kind="expense", cat="overig", merchant="Shop", **kw):
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        merchant=merchant,
        category=cat,
        status=kw.pop("status", "received" if kind == "income" else "paid"),
        expense_date=datetime(2026, month, day, 12, tzinfo=UTC),
        **kw,
    )


async def _member(lab: Lab) -> Member:
    async with AsyncSessionLocal() as s:
        return await s.get(Member, lab.member_id)


async def _entries(lab: Lab) -> list[Expense]:
    return [
        r[0]
        for r in await lab.rows(
            select(Expense).where(Expense.member_id == lab.member_id).order_by(Expense.created_at)
        )
    ]


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        return str(m.dashboard_token)


# ── mode and tags ─────────────────────────────────────────────────────────────


@db
async def test_mode_switch_and_tag_needs_business_mode(lab: Lab) -> None:
    await lab.add(_e(lab, 100))
    assert "ligue com *modo empresa*" in (await lab.say("foi da empresa")).lower()
    assert "Modo empresa ligado" in await lab.say("modo empresa")
    assert (await _member(lab)).acct_mode == "business"
    reply = await lab.say("foi da empresa")
    assert "Marcado como da empresa: Shop (€100,00)" in reply
    assert (await _entries(lab))[0].scope == "business"
    assert "Modo pessoal" in await lab.say("modo pessoal")
    assert (await _member(lab)).acct_mode == "personal"
    assert (await _entries(lab))[0].scope == "business"  # the tags are kept


@db
async def test_back_to_personal_clears_btw_and_deductible(lab: Lab) -> None:
    await lab.add(_e(lab, 121, scope="business", btw_rate=21, deductible=False))
    await lab.say("modo empresa")
    assert "Voltou a ser pessoal" in await lab.say("foi particular")
    e = (await _entries(lab))[0]
    assert (e.scope, e.btw_rate, e.deductible) == ("personal", None, True)


@db
async def test_btw_on_the_last_business_entry(lab: Lab) -> None:
    await lab.add(_e(lab, 121))
    await lab.say("modo empresa")
    assert "precisa estar marcado" in await lab.say("btw 21")  # still personal
    await lab.say("foi da empresa")
    assert "BTW 21%" in (reply := await lab.say("btw 21")) and "€21,00" in reply
    assert (await _entries(lab))[0].btw_rate == 21
    assert "Use 21, 9 ou 0" in await lab.say("btw 7")
    assert (await _entries(lab))[0].btw_rate == 21
    await lab.say("btw 9%")
    assert (await _entries(lab))[0].btw_rate == 9
    await lab.say("btw 0")
    assert (await _entries(lab))[0].btw_rate == 0


@db
async def test_not_deductible_and_back(lab: Lab) -> None:
    await lab.add(_e(lab, 80, scope="business"))
    await lab.say("modo empresa")
    assert "fora do lucro" in await lab.say("não dedutível")
    assert (await _entries(lab))[0].deductible is False
    assert "conta como despesa dedutível" in await lab.say("dedutível")
    assert (await _entries(lab))[0].deductible is True


@db
async def test_income_can_be_business_too(lab: Lab) -> None:
    await lab.add(_e(lab, 1210, kind="income", cat="inkomen", merchant="Cliente BV"))
    await lab.say("modo empresa")
    await lab.say("foi da empresa")
    await lab.say("btw 21")
    e = (await _entries(lab))[0]
    assert (e.scope, e.btw_rate, e.transaction_type) == ("business", 21, "income")


@db
async def test_no_entry_to_mark(lab: Lab) -> None:
    await lab.say("modo empresa")
    assert "Não achei um lançamento" in await lab.say("foi da empresa")


@db
async def test_the_last_entry_is_the_one_marked_not_an_older_one(lab: Lab) -> None:
    await lab.add(_e(lab, 10, merchant="Old"))
    await lab.add(_e(lab, 20, merchant="New"))
    await lab.say("modo empresa")
    await lab.say("foi da empresa")
    by = {e.merchant: e.scope for e in await _entries(lab)}
    assert by == {"Old": "personal", "New": "business"}


# ── numbers the member gives ──────────────────────────────────────────────────


@db
async def test_tax_reserve(lab: Lab) -> None:
    assert "30%" in await lab.say("reserva de imposto 30%")
    assert (await _member(lab)).tax_reserve_pct == 30
    assert "de 1 a 60" in await lab.say("reserva de imposto 90%")
    assert (await _member(lab)).tax_reserve_pct == 30
    assert "desligada" in await lab.say("reserva de imposto 0")
    assert (await _member(lab)).tax_reserve_pct is None
    assert "30%" in await lab.say("separar 30% de imposto")


@db
async def test_emergency_fund_amount_formats(lab: Lab) -> None:
    assert "€5.000,00" in await lab.say("reserva de emergência 5000")
    assert float((await _member(lab)).savings_amount) == 5000.0
    assert "€2.500,50" in await lab.say("minha reserva de emergência é 2.500,50")
    assert float((await _member(lab)).savings_amount) == 2500.5
    assert "Não entendi o valor" in await lab.say("reserva de emergência ,,.")
    assert float((await _member(lab)).savings_amount) == 2500.5


@db
async def test_always_business_categories_apply_to_new_entries(lab: Lab) -> None:
    assert "modo empresa" in (await lab.say("sempre da empresa: software")).lower()
    await lab.say("modo empresa")
    assert "Não reconheci" in await lab.say("sempre da empresa: banana")
    assert "Combinado" in await lab.say("sempre da empresa: supermercado, transporte")
    assert (await _member(lab)).business_categories == ["supermarkt", "transport"]
    lab.expense.return_value = {
        "amount": 20.0, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt",
        "description": None, "type": "expense", "days_ago": 0,
    }  # fmt: skip
    await lab.say("jumbo 20")
    lab.expense.return_value = {
        **lab.expense.return_value,
        "category": "restaurant",
        "merchant": "Bar",
    }
    await lab.say("bar 20")
    assert [e.scope for e in await _entries(lab)] == ["business", "personal"]
    assert "Pronto" in await lab.say("nada sempre da empresa")
    assert (await _member(lab)).business_categories is None


@db
async def test_personal_mode_ignores_business_categories(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.business_categories = ["supermarkt"]
        await s.commit()
    lab.expense.return_value = {
        "amount": 20.0, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt",
        "description": None, "type": "expense", "days_ago": 0,
    }  # fmt: skip
    await lab.say("jumbo 20")
    assert (await _entries(lab))[0].scope == "personal"


# ── summary in the chat ───────────────────────────────────────────────────────


@db
async def test_chat_summary_personal_and_business(lab: Lab) -> None:
    assert "ainda não há lançamentos" in (await lab.say("contabilidade")).lower()
    await lab.add(
        _e(lab, 3000, kind="income", cat="inkomen", merchant="Job", month=9),
        _e(lab, 1000, cat="wonen", merchant="Huur", month=9),
        _e(
            lab,
            1210,
            kind="income",
            cat="inkomen",
            merchant="Cliente",
            month=10,
            scope="business",
            btw_rate=21,
        ),
        _e(lab, 121, cat="overig", merchant="Laptop", month=10, scope="business", btw_rate=21),
        _e(lab, 50, cat="overig", merchant="Café", month=10, scope="business"),
    )
    reply = await lab.say("contabilidade")
    assert (
        "Contabilidade 2026" in reply
        and "Entradas €4.210,00" in reply
        and "Saídas €1.171,00" in reply
    )
    assert "Empresa" not in reply  # personal mode
    await lab.say("modo empresa")
    await lab.say("reserva de imposto 30%")
    reply = await lab.say("contabilidade")
    assert (
        "receita €1.000,00" in reply
        and "despesas dedutíveis €150,00" in reply
        and "lucro €850,00" in reply
    )
    assert "4º trimestre: €189,00" in reply  # 210 owed - 21 input
    assert "Sem BTW informado: 1 lançamento da empresa" in reply
    assert "Reserva de imposto (30%): €255,00" in reply
    assert "estimativa" in reply.lower()


# ── panel ─────────────────────────────────────────────────────────────────────


@db
async def test_books_tab_is_listed_and_personal_cards_for_everyone(lab: Lab, client) -> None:
    tok = await _token(lab.member_id)
    assert 'data-tab="books"' in (await client.get(f"/d/{tok}")).text
    empty = (await client.get(f"/api/d/{tok}/books")).json()
    assert empty["tab"] == "books"
    assert [c["id"] for c in empty["cards"]] == [
        "books_year",
        "books_categories",
        "books_fixed",
        "books_emergency",
    ]
    assert all(c["empty"] for c in empty["cards"])


@db
async def test_books_cards_numbers(lab: Lab, client) -> None:
    await lab.add(
        _e(lab, 3000, kind="income", cat="inkomen", merchant="Job", month=9, day=25),
        _e(lab, 3000, kind="income", cat="inkomen", merchant="Job", month=10, day=1),
        _e(lab, 1000, cat="wonen", merchant="Huur", month=9, day=1),
        _e(lab, 1000, cat="wonen", merchant="Huur", month=10, day=1),
        _e(lab, 300, cat="supermarkt", merchant="Jumbo", month=9, day=10),
        _e(lab, 200, cat="supermarkt", merchant="Jumbo", month=10, day=10),
        _e(lab, 500, cat="restaurant", merchant="Bar", month=8, day=3),
        _e(lab, 77, cat="overig", merchant="Pending", month=10, day=25, status="to_pay"),
    )
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.savings_amount = 5000
        await s.commit()
    tok = await _token(lab.member_id)
    cards = {c["id"]: c for c in (await client.get(f"/api/d/{tok}/books")).json()["cards"]}
    y = cards["books_year"]["values"]
    assert (y["year"], y["income"], y["expense"], y["balance"]) == (2026, 6000.0, 3000.0, 3000.0)
    assert y["saving_rate"] == 50.0 and y["business"] is False
    assert [m["m"] for m in cards["books_year"]["months"]] == list(range(1, 11))  # through October
    assert cards["books_year"]["months"][8]["balance"] == 1700.0  # September: 3000 - 1000 - 300
    cat = cards["books_categories"]
    assert [i["category"] for i in cat["items"]] == [
        "wonen",
        "restaurant",
        "supermarkt",
    ]  # a tie sorts by name
    assert cat["items"][0]["amount"] == 2000.0 and cat["items"][0]["pct"] == 66.7
    f = cards["books_fixed"]["values"]
    assert (f["fixed"], f["variable"], f["fixed_pct"]) == (2000.0, 1000.0, 66.7)
    e = cards["books_emergency"]["values"]
    # last 3 full months before October: Jul, Aug, Sep = 500 + 1300 = 1800 → 600 a month
    assert (e["savings"], e["avg_monthly"], e["months"]) == (5000.0, 600.0, 8.3)
    assert "Pending" not in str(cards)  # an unsettled bill is not in the accounting


@db
async def test_business_cards_only_in_business_mode(lab: Lab, client) -> None:
    await lab.add(
        _e(
            lab,
            1210,
            kind="income",
            cat="inkomen",
            merchant="Cliente",
            month=5,
            day=3,
            scope="business",
            btw_rate=21,
        ),
        _e(lab, 121, merchant="Laptop", month=5, day=9, scope="business", btw_rate=21),
        _e(lab, 60, cat="transport", merchant="Trein", month=8, day=9, scope="business"),
        _e(
            lab,
            80,
            merchant="Boete",
            month=8,
            day=10,
            scope="business",
            btw_rate=21,
            deductible=False,
        ),
        _e(lab, 500, cat="wonen", merchant="Huur", month=5, day=1),
    )
    tok = await _token(lab.member_id)
    personal = (await client.get(f"/api/d/{tok}/books")).json()
    assert [c["id"] for c in personal["cards"]] == [
        "books_year",
        "books_categories",
        "books_fixed",
        "books_emergency",
    ]
    await lab.say("modo empresa")
    await lab.say("reserva de imposto 30%")
    body = (await client.get(f"/api/d/{tok}/books")).json()
    cards = {c["id"]: c for c in body["cards"]}
    assert list(cards)[-4:] == ["books_pl", "books_btw", "books_deductible", "books_reserve"]
    pl = cards["books_pl"]["values"]
    assert (pl["income"], pl["expense"], pl["profit"]) == (
        1000.0,
        160.0,
        840.0,
    )  # 121-21 and 60 as typed
    assert (pl["non_deductible"], pl["unrated"]) == (80.0, 1)
    q = {x["q"]: x for x in cards["books_btw"]["quarters"]}
    assert (q[2]["owed"], q[2]["input"], q[2]["net"]) == (210.0, 21.0, 189.0)
    assert q[3]["net"] == 0.0 and q[3]["unrated"] == 1
    assert cards["books_btw"]["values"]["current_quarter"] == 4
    ded = cards["books_deductible"]
    assert [(i["category"], i["amount"]) for i in ded["items"]] == [
        ("overig", 121.0),
        ("transport", 60.0),
    ]
    r = cards["books_reserve"]["values"]
    assert (r["pct"], r["profit"], r["reserve"]) == (30, 840.0, 252.0)


@db
async def test_business_cards_without_tags_explain_what_to_do(lab: Lab, client) -> None:
    await lab.add(_e(lab, 10))
    await lab.say("modo empresa")
    tok = await _token(lab.member_id)
    cards = {c["id"]: c for c in (await client.get(f"/api/d/{tok}/books")).json()["cards"]}
    assert cards["books_pl"]["empty"] and "foi da empresa" in cards["books_pl"]["hint"]["chat"]
    assert (
        cards["books_reserve"]["empty"]
        and "reserva de imposto" in cards["books_reserve"]["hint"]["chat"]
    )


@db
async def test_year_parameter_and_other_members_data(lab: Lab, lab2: Lab, client) -> None:
    await lab.add(
        _e(lab, 100, month=10),
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=40,
            merchant="Old",
            category="overig",
            status="paid",
            expense_date=datetime(2025, 3, 3, 12, tzinfo=UTC),
        ),
    )
    await lab2.add(_e(lab2, 999, merchant="SEGREDO"))
    tok = await _token(lab.member_id)
    now = (await client.get(f"/api/d/{tok}/books")).json()["cards"][0]
    assert now["values"]["expense"] == 100.0
    last = (await client.get(f"/api/d/{tok}/books?year=2025")).json()["cards"][0]
    assert last["values"]["expense"] == 40.0 and len(last["months"]) == 12
    bad = (await client.get(f"/api/d/{tok}/books?year=abc")).json()["cards"][0]
    assert bad["values"]["year"] == 2026
    future = (await client.get(f"/api/d/{tok}/books?year=2999")).json()["cards"][0]
    assert future["values"]["year"] == 2026
    assert "SEGREDO" not in str((await client.get(f"/api/d/{tok}/books")).text)


@db
async def test_opening_the_tab_is_audited_without_content(lab: Lab, client) -> None:
    await lab.add(_e(lab, 77, merchant="Hemelsbreed"))
    tok = await _token(lab.member_id)
    await client.get(f"/api/d/{tok}/books")
    rows = await lab.rows(
        select(AuditLog.event, AuditLog.detail).where(AuditLog.member_id == lab.member_id)
    )
    assert "panel_books_opened" in {r[0] for r in rows}
    assert "Hemelsbreed" not in str(rows)


# ── CSV for the accountant ────────────────────────────────────────────────────


async def _csv(lab: Lab, client, command: str) -> tuple[str, str]:
    reply = await lab.say(command)
    m = re.search(r"https://alfred\.test(/api/d/[^ \n]+)", reply)
    assert m, reply
    path = m.group(1)
    page = await client.get(path)
    assert page.status_code == 200 and "<form" in page.text
    out = await client.post(path)
    assert out.status_code == 200
    return reply, out.text


@db
async def test_csv_export_business_mode_has_only_business_entries(lab: Lab, client) -> None:
    await lab.add(
        _e(
            lab,
            1210,
            kind="income",
            cat="inkomen",
            merchant="Cliente",
            month=5,
            day=3,
            scope="business",
            btw_rate=21,
        ),
        _e(lab, 60, cat="transport", merchant="=EVIL()", month=8, day=9, scope="business"),
        _e(lab, 500, cat="wonen", merchant="Huur Privada", month=5, day=1),
    )
    await lab.say("modo empresa")
    reply, body = await _csv(lab, client, "exportar contabilidade")
    assert "só com os lançamentos da empresa" in reply
    lines = body.lstrip("﻿").splitlines()
    assert lines[0].startswith(
        "date,type,scope,merchant,category,description,amount_gross,btw_rate,btw_amount,amount_net"
    )
    assert len(lines) == 3 and "Huur Privada" not in body
    assert (
        "2026-05-03,income,business,Cliente,inkomen,,1210.00,21,210.00,1000.00,yes,received"
        in lines[1]
    )
    assert "'=EVIL()" in lines[2]  # no formula in a spreadsheet


@db
async def test_csv_export_personal_mode_has_everything_of_the_year(lab: Lab, client) -> None:
    await lab.add(
        _e(lab, 5, merchant="A", month=3), _e(lab, 6, merchant="B", month=10, scope="business")
    )
    _, body = await _csv(lab, client, "exportar contabilidade 2026")
    assert body.count("\n") == 3


@db
async def test_csv_link_is_single_use_and_the_year_is_checked(lab: Lab, client) -> None:
    reply = await lab.say("exportar contabilidade")
    path = re.search(r"https://alfred\.test(/api/d/[^ \n]+)", reply).group(1)
    assert (await client.post(path)).status_code == 200
    assert (await client.post(path)).status_code == 404
    assert (await client.get(path)).status_code == 404
    assert "Não tenho esse ano" in await lab.say("exportar contabilidade 1999")


@db
async def test_csv_export_year_parameter(lab: Lab, client) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=40,
            merchant="Old",
            category="overig",
            status="paid",
            expense_date=datetime(2025, 3, 3, 12, tzinfo=UTC),
        )
    )
    _, body = await _csv(lab, client, "exportar contabilidade 2025")
    assert "Old" in body and body.count("\n") == 2


# ── privacy and languages ─────────────────────────────────────────────────────


@db
async def test_export_of_my_data_contains_the_accounting_fields(lab: Lab) -> None:
    await lab.add(_e(lab, 121, scope="business", btw_rate=21))
    await lab.say("modo empresa")
    await lab.say("reserva de imposto 25%")
    async with AsyncSessionLocal() as s:
        data = await export_member_data(s, await s.get(Member, lab.member_id))
    assert data["tables"]["expense"][0]["scope"] == "business"
    assert data["tables"]["expense"][0]["btw_rate"] == 21
    member = (
        next(r for r in data["tables"]["member"] if r["id"] == str(lab.member_id))
        if "member" in data["tables"]
        else None
    )
    if member is not None:
        assert member["acct_mode"] == "business" and member["tax_reserve_pct"] == 25


@db
async def test_dutch_commands_and_answers(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.language = "nl"
        await s.commit()
    await lab.add(_e(lab, 100))
    assert "Zakelijke modus aan" in await lab.say("zakelijke modus")
    assert "Als zakelijk gemarkeerd" in await lab.say("was zakelijk")
    assert "Btw 21%" in await lab.say("btw 21")
    assert "Afgesproken" in await lab.say("belastingreserve 30%")
    assert "Genoteerd" in await lab.say("noodfonds 5000")
    assert "Boekhouding" in await lab.say("boekhouding")


@db
async def test_unrelated_text_is_left_alone(lab: Lab) -> None:
    # none of these is an accounting command: they fall through to the normal handlers
    for text in (
        "btw",
        "modo",
        "contabilidade geral da empresa de ontem e de hoje e de amanha mais um pouco",
    ):
        lab.llm_reply.return_value = "[llm]"
        reply = await lab.say(text)
        assert "Modo" not in reply and "Contabilidade 2026" not in reply
