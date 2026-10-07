# ruff: noqa: E501
"""V2-12 part 2 — client invoices: register, list, mark paid (books business income), undo, delete."""

from __future__ import annotations

import os
from datetime import date

import pytest
from sqlalchemy import func, select

from alfred.conversation import _t
from alfred.db import AsyncSessionLocal
from alfred.invoices import _match, parse_due
from alfred.models import ClientInvoice, Expense, Member
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.conversation.today_local", lambda: TODAY)


async def _inv(lab: Lab) -> list[ClientInvoice]:
    rows = await lab.rows(
        select(ClientInvoice)
        .where(ClientInvoice.member_id == lab.member_id)
        .order_by(ClientInvoice.created_at)
    )
    return [r[0] for r in rows]


async def _income(lab: Lab) -> list[Expense]:
    rows = await lab.rows(
        select(Expense).where(
            Expense.member_id == lab.member_id, Expense.transaction_type == "income"
        )
    )
    return [r[0] for r in rows]


# ── pure rules ────────────────────────────────────────────────────────────────


def test_due_dates() -> None:
    assert parse_due(None, TODAY) == date(2026, 11, 13)  # default 30 days
    assert parse_due("45 dias", TODAY) == date(2026, 11, 28)
    assert parse_due("em 30 dias", TODAY) == date(2026, 11, 13)
    assert parse_due("over 20 dagen", TODAY) == date(2026, 11, 3)
    assert parse_due("in 10 days", TODAY) == date(2026, 10, 24)
    assert parse_due("15/11", TODAY) == date(2026, 11, 15)
    assert parse_due("dia 15/11/2026", TODAY) == date(2026, 11, 15)
    assert parse_due("05/10", TODAY) == date(2027, 10, 5)  # already passed this year: next year
    assert parse_due("31/02", TODAY) is None
    assert parse_due("0 dias", TODAY) is None
    assert parse_due("999 dias", TODAY) is None
    assert parse_due("amanhã talvez", TODAY) is None


def test_reference_matches_number_before_client() -> None:
    a = ClientInvoice(client="Acme", number="2026-014", amount=1, issued_on=TODAY, due_on=TODAY)
    b = ClientInvoice(client="Acme Corp", number=None, amount=1, issued_on=TODAY, due_on=TODAY)
    assert _match([a, b], "nº 2026-014") == [a]
    assert _match([a, b], "acme") == [a]  # exact name beats the partial one
    assert _match([a, b], "corp") == [b]
    assert _match([a, b], "nobody") == []


# ── through the router ────────────────────────────────────────────────────────


@db
@pytest.mark.asyncio
async def test_register_an_invoice_with_number_btw_and_due_date(lab: Lab) -> None:
    reply = await lab.say("fatura nº 2026-014 para Acme BV 1210 btw 21 vence 15/11")
    assert "Fatura registrada" in reply and "Acme BV (#2026-014)" in reply
    assert "€1.210,00" in reply and "15/11/2026" in reply
    (inv,) = await _inv(lab)
    assert (inv.client, inv.number, inv.amount, inv.btw_rate) == ("Acme BV", "2026-014", 1210, 21)
    assert (inv.issued_on, inv.due_on, inv.paid_on) == (TODAY, date(2026, 11, 15), None)
    assert [i.split(":")[0] for i, _ in lab.buttons[-1]] == ["inv_undo"]


@db
@pytest.mark.asyncio
async def test_without_btw_or_due_it_uses_the_defaults_and_says_so(lab: Lab) -> None:
    reply = await lab.say("fatura para Jan de Vries 300")
    (inv,) = await _inv(lab)
    assert inv.btw_rate is None and inv.due_on == date(2026, 11, 13)
    assert inv.client == "Jan de Vries"  # the member's own capitalisation
    assert "Sem BTW informado" in reply


@db
@pytest.mark.asyncio
async def test_works_in_the_other_languages(lab: Lab) -> None:
    await lab.say("invoice no 7 to Globex 500 btw 9 due in 10 days")
    await lab.say("factuur nr 8 voor Initech 250 btw 21 vervalt over 20 dagen")
    rows = await _inv(lab)
    assert [(r.number, r.client, r.btw_rate) for r in rows] == [
        ("7", "Globex", 9),
        ("8", "Initech", 21),
    ]
    assert rows[0].due_on == date(2026, 10, 24)


@db
@pytest.mark.asyncio
async def test_bad_input_is_refused_and_nothing_is_saved(lab: Lab) -> None:
    assert "BTW pode ser 0, 9 ou 21" in await lab.say("fatura para Acme 100 btw 13")
    assert "Não entendi o vencimento" in await lab.say("fatura para Acme 100 vence 31/02")
    assert "Não consegui ler o valor" in await lab.say("fatura para Acme 99999999999")
    assert await _inv(lab) == []


@db
@pytest.mark.asyncio
async def test_duplicate_number_is_refused(lab: Lab) -> None:
    await lab.say("fatura nº 1 para Acme 100")
    reply = await lab.say("fatura nº 1 para Globex 200")
    assert "já tem uma fatura com o número 1" in reply
    assert len(await _inv(lab)) == 1


@db
@pytest.mark.asyncio
async def test_list_shows_open_and_overdue_with_totals(lab: Lab) -> None:
    await lab.add(
        ClientInvoice(
            member_id=lab.member_id,
            client="Acme",
            number="1",
            amount=1000,
            issued_on=date(2026, 8, 1),
            due_on=date(2026, 9, 1),
        ),
        ClientInvoice(
            member_id=lab.member_id,
            client="Globex",
            amount=500,
            issued_on=TODAY,
            due_on=date(2026, 11, 13),
        ),
        ClientInvoice(
            member_id=lab.member_id,
            client="Paid Co",
            amount=77,
            issued_on=date(2026, 8, 1),
            due_on=date(2026, 9, 1),
            paid_on=date(2026, 9, 2),
        ),
    )
    reply = await lab.say("faturas")
    assert "Acme (#1): €1.000,00 · venceu 01/09" in reply
    assert "Globex: €500,00 · vence 13/11" in reply
    assert "Paid Co" not in reply
    assert "Em aberto: €1.500,00 (2)" in reply and "Em atraso: €1.000,00 (1)" in reply


@db
@pytest.mark.asyncio
async def test_empty_list_explains_how_to_register(lab: Lab) -> None:
    assert "fatura para Acme" in await lab.say("minhas faturas")


@db
@pytest.mark.asyncio
async def test_marking_paid_books_business_income_with_the_btw_rate(lab: Lab) -> None:
    await lab.say("fatura nº 5 para Acme 1210 btw 21")
    reply = await lab.say("fatura 5 paga")
    assert "marcada como paga" in reply and "€1.210,00" in reply
    (inv,) = await _inv(lab)
    assert inv.paid_on == TODAY and inv.income_id is not None
    (inc,) = await _income(lab)
    assert (inc.amount, inc.scope, inc.btw_rate, inc.status, inc.merchant) == (
        1210,
        "business",
        21,
        "received",
        "Acme",
    )
    assert inc.id == inv.income_id
    assert await lab.say("fatura 5 paga") == _t("inv_already_paid", "pt")
    assert len(await _income(lab)) == 1  # never booked twice


@db
@pytest.mark.asyncio
async def test_other_ways_to_say_paid(lab: Lab) -> None:
    await lab.say("fatura para Acme 100")
    await lab.say("fatura para Globex 200")
    await lab.say("fatura para Initech 300")
    await lab.say("Acme pagou a fatura")
    await lab.say("recebi a fatura globex")
    await lab.say("invoice initech paid")
    assert all(r.paid_on == TODAY for r in await _inv(lab))
    assert len(await _income(lab)) == 3


@db
@pytest.mark.asyncio
async def test_unpay_button_reopens_and_removes_the_income(lab: Lab) -> None:
    await lab.say("fatura nº 5 para Acme 1210 btw 21")
    await lab.say("fatura 5 paga")
    unpay = lab.buttons[-1][0][0]
    assert unpay.startswith("inv_unpay:")
    assert "reaberta" in await lab.tap(unpay)
    (inv,) = await _inv(lab)
    assert inv.paid_on is None and inv.income_id is None
    assert await _income(lab) == []
    assert await lab.tap(unpay) == _t("button_gone", "pt")  # a second tap does nothing


@db
@pytest.mark.asyncio
async def test_undo_button_removes_only_an_open_invoice(lab: Lab) -> None:
    await lab.say("fatura nº 1 para Acme 100")
    undo = lab.buttons[-1][0][0]
    assert "apagada" in await lab.tap(undo)
    assert await _inv(lab) == []
    await lab.say("fatura nº 2 para Acme 100")
    undo2 = lab.buttons[-1][0][0]
    await lab.say("fatura 2 paga")
    assert await lab.tap(undo2) == _t("button_gone", "pt")  # paid: use the unpay button instead
    assert len(await _inv(lab)) == 1


@db
@pytest.mark.asyncio
async def test_ambiguous_and_unknown_references_do_nothing(lab: Lab) -> None:
    await lab.say("fatura para Acme Norte 100")
    await lab.say("fatura para Acme Sul 200")
    assert "mais de uma" in await lab.say("fatura acme paga")
    assert "Não achei essa fatura" in await lab.say("fatura zzz paga")
    assert await _income(lab) == []


@db
@pytest.mark.asyncio
async def test_delete_an_invoice_keeps_income_already_booked(lab: Lab) -> None:
    await lab.say("fatura nº 3 para Acme 100")
    await lab.say("fatura 3 paga")
    assert "apagada" in await lab.say("apaga a fatura 3")
    assert await _inv(lab) == []
    assert len(await _income(lab)) == 1  # the money was received; only the invoice record goes


@db
@pytest.mark.asyncio
async def test_invoices_are_private_to_their_member(lab: Lab, lab2: Lab) -> None:
    await lab2.say("fatura nº 9 para Secret 999")
    assert "Secret" not in await lab.say("faturas")
    assert "Não achei essa fatura" in await lab.say("fatura 9 paga")
    assert await lab2.scalar(select(func.count()).select_from(ClientInvoice)) >= 1


@db
@pytest.mark.asyncio
async def test_accounting_summary_mentions_what_clients_owe(lab: Lab) -> None:
    await lab.add(
        ClientInvoice(
            member_id=lab.member_id,
            client="Acme",
            amount=800,
            issued_on=date(2026, 8, 1),
            due_on=date(2026, 9, 1),
        ),
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="income",
            amount=100,
            category="inkomen",
            status="received",
            expense_date=__import__("datetime").datetime(
                2026, 10, 2, 12, tzinfo=__import__("datetime").UTC
            ),
        ),
    )
    reply = await lab.say("contabilidade")
    assert "A receber de clientes: €800,00 (1 faturas), €800,00 em atraso" in reply


@db
@pytest.mark.asyncio
async def test_invoice_text_is_not_mistaken_for_other_commands(lab: Lab) -> None:
    # hostile client name: markup is stripped, length capped, nothing executed
    await lab.say("fatura para *Acme* `x` " + "a" * 100 + " 100")
    (inv,) = await _inv(lab)
    assert "*" not in inv.client and "`" not in inv.client and len(inv.client) <= 60


@db
@pytest.mark.asyncio
async def test_exported_and_erased_with_the_member(lab: Lab) -> None:
    await lab.say("fatura nº 1 para Acme 100")
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        assert "client_invoice" in str(await export_member_data(s, member))
        await erase_member(s, member)
        await s.commit()
    assert (
        await lab.scalar(
            select(func.count())
            .select_from(ClientInvoice)
            .where(ClientInvoice.member_id == lab.member_id)
        )
        == 0
    )


@db
@pytest.mark.asyncio
async def test_panel_card_lists_open_invoices_only_when_there_are_some(lab: Lab) -> None:
    from alfred.accounting import panel_cards

    async def cards() -> list[dict]:
        async with AsyncSessionLocal() as s:
            member = await s.get(Member, lab.member_id)
            return [
                c
                for c in await panel_cards(s, member, "pt", TODAY)
                if c["id"] == "books_receivables"
            ]

    assert await cards() == []
    await lab.add(
        ClientInvoice(
            member_id=lab.member_id,
            client="Acme",
            number="1",
            amount=800,
            issued_on=date(2026, 8, 1),
            due_on=date(2026, 9, 1),
        ),
        ClientInvoice(
            member_id=lab.member_id,
            client="Done",
            amount=5,
            issued_on=date(2026, 8, 1),
            due_on=date(2026, 9, 1),
            paid_on=date(2026, 9, 2),
        ),
    )
    (card,) = await cards()
    assert card["values"] == {"open": 800.0, "count": 1, "overdue": 800.0, "overdue_count": 1}
    assert card["items"] == [
        {"label": "Acme (#1)", "amount": 800.0, "due": "2026-09-01", "overdue": True}
    ]
