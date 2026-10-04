# ruff: noqa: E501
"""V2-10 — couple mode: invite, join with consent, "da casa" rows, balance, settle, split, leave."""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update

from alfred import couple, panel_tokens
from alfred.db import AsyncSessionLocal
from alfred.models import AuditLog, Expense, Member, PartnerLink, PartnerSettlement
from alfred.privacy import export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)


@pytest.fixture
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.panel_api.today_local", lambda: TODAY)


def _at(day: int) -> datetime:
    return datetime(2026, 10, day, 12, tzinfo=UTC)


def _exp(
    lab: Lab, amount: float, *, merchant="Jumbo", cat="supermarkt", day=10, shared=False, **kw
):
    return Expense(
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kw.pop("kind", "expense"),
        amount=amount,
        merchant=merchant,
        category=cat,
        status=kw.pop("status", "paid"),
        expense_date=_at(day),
        shared=shared,
        **kw,
    )


async def _link(lab: Lab, lab2: Lab, pct: int = 50) -> uuid.UUID:
    """An active link: ``lab`` invited, ``lab2`` joined."""
    link = PartnerLink(
        inviter_id=lab.member_id,
        partner_id=lab2.member_id,
        code="TEST" + uuid.uuid4().hex[:2].upper(),
        status="active",
        inviter_pct=pct,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        accepted_at=datetime.now(UTC),
    )
    await lab.add(link)
    return link.id


async def _code(lab: Lab) -> str:
    reply = await lab.say("convidar parceiro")
    m = re.search(r"\*entrar casa ([A-Z0-9]{6})\*", reply)
    assert m, reply
    return m.group(1)


# ── pure ──────────────────────────────────────────────────────────────────────


def test_net_even_split_the_one_who_paid_more_is_owed_half_the_difference() -> None:
    net = couple.compute_net(Decimal(100), Decimal(40), 50, Decimal(0), Decimal(0))
    assert net == Decimal("30.00")  # total 140, each owes 70; the inviter paid 100


def test_net_uneven_split_and_settlements() -> None:
    # 70/30: total 100 all paid by the inviter, so the partner owes 30
    assert couple.compute_net(Decimal(100), Decimal(0), 70, Decimal(0), Decimal(0)) == Decimal(30)
    # the partner paid 30 back: even
    assert couple.compute_net(Decimal(100), Decimal(0), 70, Decimal(0), Decimal(30)) == Decimal(0)


def test_net_rounds_to_cents_and_stays_symmetric() -> None:
    net = couple.compute_net(Decimal("10.01"), Decimal(0), 50, Decimal(0), Decimal(0))
    assert net == Decimal("5.00")  # 5.005 rounds half up for the share, 10.01 - 5.01
    assert net == Decimal("5.00") or net == Decimal("5.01")


def test_parse_categories_accepts_words_people_use() -> None:
    assert couple.parse_categories("mercado e aluguel") == ["supermarkt", "wonen"]
    assert couple.parse_categories("groceries, rent") == ["supermarkt", "wonen"]
    assert couple.parse_categories("supermercado") == ["supermarkt"]
    assert couple.parse_categories("salario") == []  # never income
    assert couple.parse_categories("overig") == []


# ── invite and join ───────────────────────────────────────────────────────────


@db
async def test_invite_gives_a_code_and_repeats_it(lab: Lab) -> None:
    code = await _code(lab)
    assert await _code(lab) == code
    assert await lab.scalar(select(func.count()).select_from(PartnerLink)) >= 1
    row = (await lab.rows(select(PartnerLink).where(PartnerLink.code == code)))[0][0]
    assert row.status == "invited" and row.partner_id is None
    assert row.expires_at > datetime.now(UTC) + timedelta(days=6)


@db
async def test_join_needs_the_partner_to_accept(lab: Lab, lab2: Lab) -> None:
    code = await _code(lab)
    reply = await lab2.say(f"entrar casa {code.lower()}")
    assert "convidou você" in reply and "da casa" in reply
    assert [b[0].split(":")[0] for b in lab2.buttons[-1]] == ["couple_yes", "couple_no"]
    link = (await lab.rows(select(PartnerLink).where(PartnerLink.code == code)))[0][0]
    assert link.status == "pending" and link.partner_id == lab2.member_id
    # nothing is shared until the tap
    assert await lab.say("gastos da casa") != ""
    assert "ainda não divide" in lab.sent[-1]

    done = await lab2.tap(f"couple_yes:{link.id}")
    assert "dividem o financeiro" in done
    after = (await lab.rows(select(PartnerLink).where(PartnerLink.code == code)))[0][0]
    assert after.status == "active" and after.accepted_at is not None
    assert "Total da casa" in await lab.say("gastos da casa")


@db
async def test_decline_ends_the_invitation(lab: Lab, lab2: Lab) -> None:
    code = await _code(lab)
    await lab2.say(f"entrar casa {code}")
    link = (await lab.rows(select(PartnerLink).where(PartnerLink.code == code)))[0][0]
    assert "não compartilhei nada" in await lab2.tap(f"couple_no:{link.id}")
    assert await couple_status(lab, code) == "ended"
    assert "ainda não divide" in await lab2.say("gastos da casa")


async def couple_status(lab: Lab, code: str) -> str:
    return (await lab.rows(select(PartnerLink.status).where(PartnerLink.code == code)))[0][0]


@db
async def test_a_stranger_cannot_accept_someone_elses_invitation(lab: Lab, lab2: Lab) -> None:
    code = await _code(lab)
    await lab2.say(f"entrar casa {code}")
    link = (await lab.rows(select(PartnerLink).where(PartnerLink.code == code)))[0][0]
    # the inviter taps the partner's button (or a forged id): nothing happens
    assert "não vale mais" in await lab.tap(f"couple_yes:{link.id}")
    assert await couple_status(lab, code) == "pending"


@db
async def test_wrong_or_expired_code_is_refused_the_same_way(lab: Lab, lab2: Lab) -> None:
    wrong = await lab2.say("entrar casa ZZZZZZ")
    assert "não vale" in wrong
    code = await _code(lab)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(PartnerLink)
            .where(PartnerLink.code == code)
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    assert "não vale" in await lab2.say(f"entrar casa {code}")


@db
async def test_own_code_and_too_many_wrong_codes(lab: Lab, lab2: Lab) -> None:
    code = await _code(lab)
    assert "esse código é seu" in (await lab.say(f"entrar casa {code}")).lower()
    for _ in range(couple.MAX_JOIN_FAILS):
        assert "não vale" in await lab2.say("entrar casa AAAAAA")
    assert "Muitas tentativas" in await lab2.say(f"entrar casa {code}")  # even the right one


@db
async def test_someone_already_in_a_home_cannot_invite_or_join(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    assert "já divide o financeiro" in await lab.say("convidar parceiro")
    assert "já divide o financeiro" in await lab2.say("entrar casa ABCDEF")


# ── marking ───────────────────────────────────────────────────────────────────


@db
async def test_marking_needs_a_partner(lab: Lab) -> None:
    await lab.add(_exp(lab, 20))
    assert "ainda não divide" in await lab.say("foi da casa")


@db
async def test_mark_last_expense_as_home_and_back(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 45, merchant="Albert Heijn"))
    reply = await lab.say("foi da casa")
    assert "Albert Heijn" in reply and "45,00" in reply
    assert (
        await lab.scalar(select(Expense.shared).where(Expense.member_id == lab.member_id)) is True
    )
    assert "pessoal" in await lab.say("foi pessoal")
    assert (
        await lab.scalar(select(Expense.shared).where(Expense.member_id == lab.member_id)) is False
    )


@db
async def test_confirmation_has_a_home_button_only_for_couples(lab: Lab, lab2: Lab) -> None:
    lab.expense.return_value = {
        "amount": 12.5, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt",
        "description": None, "type": "expense", "days_ago": 0,
    }  # fmt: skip
    await lab.say("jumbo 12,50")
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["edit", "undo"]

    await _link(lab, lab2)
    await lab.say("jumbo 12,50")
    ids = [b[0].split(":")[0] for b in lab.buttons[-1]]
    assert ids == ["edit", "undo", "home"]
    row_id = lab.buttons[-1][2][0].split(":")[1]
    assert "da casa" in (await lab.tap(f"home:{row_id}")).lower()
    shared = await lab.scalar(select(Expense.shared).where(Expense.id == uuid.UUID(row_id)))
    assert shared is True


@db
async def test_a_button_of_someone_elses_expense_does_nothing(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    other = _exp(lab2, 30)
    await lab2.add(other)
    await lab.tap(f"home:{other.id}")
    assert await lab.scalar(select(Expense.shared).where(Expense.id == other.id)) is False


@db
async def test_always_home_categories_apply_to_new_expenses(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    assert "supermercado" in (await lab.say("sempre da casa: mercado")).lower()
    lab.expense.return_value = {
        "amount": 20.0, "currency": "EUR", "merchant": "Jumbo", "category": "supermarkt",
        "description": None, "type": "expense", "days_ago": 0,
    }  # fmt: skip
    reply = await lab.say("jumbo 20")
    assert "(da casa)" in reply
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["edit", "undo"]  # already home
    lab.expense.return_value["category"] = "restaurant"
    lab.expense.return_value["merchant"] = "Bar"
    assert "(da casa)" not in await lab.say("bar 20")
    assert "não reconheci" in (await lab.say("sempre da casa: banana")).lower()
    assert "nenhuma categoria" in await lab.say("nada sempre da casa")


@db
async def test_income_cannot_be_home(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 1000, merchant="Salário", cat="inkomen", kind="income"))
    assert "Não achei" in await lab.say("foi da casa")  # an income is never "da casa"


# ── balance, settle, split ────────────────────────────────────────────────────


@db
async def test_summary_balance_and_both_views(lab: Lab, lab2: Lab, today) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True), _exp(lab, 500, merchant="Privado"))
    await lab2.add(_exp(lab2, 40, shared=True, merchant="Etos"))
    mine = await lab.say("gastos da casa")
    assert "Total da casa: €140,00" in mine and "Você pagou €100,00" in mine
    assert "te deve €30,00" in mine and "Privado" not in mine and "500" not in mine
    theirs = await lab2.say("gastos da casa")
    assert "Você deve €30,00" in theirs and "Você pagou €40,00" in theirs


@db
async def test_pending_bills_do_not_count_until_paid(lab: Lab, lab2: Lab, today) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True, status="to_pay"))
    assert "Total da casa: €0,00" in await lab.say("gastos da casa")


@db
async def test_split_changes_the_balance_for_both(lab: Lab, lab2: Lab, today) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True))
    assert "70% e" in await lab.say("divisão 70/30")  # my share first
    assert "te deve €30,00" in await lab.say("gastos da casa")
    assert "Você deve €30,00" in await lab2.say("gastos da casa")
    # the partner says 70/30 too: now the partner pays 70 and the inviter 30
    await lab2.say("divisão 70/30")
    assert "Você deve €70,00" in await lab2.say("gastos da casa")
    inviter_pct = await lab.scalar(
        select(PartnerLink.inviter_pct).where(PartnerLink.inviter_id == lab.member_id)
    )
    assert inviter_pct == 30


@db
async def test_split_must_add_up(lab: Lab, lab2: Lab) -> None:
    await _link(lab, lab2)
    assert "somem 100" in await lab.say("divisão 60/60")
    assert "somem 100" in await lab.say("divisão 100/0")


@db
async def test_settle_records_the_debtors_payment_and_brings_balance_to_zero(
    lab: Lab, lab2: Lab, today
) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True))
    reply = await lab2.say("acertamos")  # the partner owes 50
    assert "você pagou €50,00" in reply
    assert "em dia" in await lab.say("gastos da casa")
    assert "já estão em dia" in await lab.say("acertamos")
    rows = await lab.rows(select(PartnerSettlement))
    assert [
        (r[0].member_id, Decimal(str(r[0].amount)))
        for r in rows
        if r[0].member_id == lab2.member_id
    ] == [(lab2.member_id, Decimal("50.00"))]
    # a new shared expense starts a new debt on top of the settled one
    await lab2.add(_exp(lab2, 30, shared=True))
    assert "Você deve €15,00" in await lab.say("gastos da casa")


@db
async def test_settle_by_the_creditor_names_the_debtor(lab: Lab, lab2: Lab, today) -> None:
    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True))
    await lab.say("acertamos")
    assert (
        await lab.scalar(
            select(PartnerSettlement.member_id).where(PartnerSettlement.link_id.is_not(None))
        )
        == lab2.member_id
    )


# ── leaving ───────────────────────────────────────────────────────────────────


@db
async def test_leave_asks_first_then_makes_everything_private(lab: Lab, lab2: Lab) -> None:
    link_id = await _link(lab, lab2)
    await lab.add(_exp(lab, 10, shared=True))
    await lab2.add(_exp(lab2, 20, shared=True))
    ask = await lab.say("sair da casa")
    assert "Quer mesmo sair" in ask
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["couple_end", "couple_keep"]
    assert "continuo com a casa" in await lab.tap(f"couple_keep:{link_id}")
    assert await lab.scalar(select(PartnerLink.status).where(PartnerLink.id == link_id)) == "active"

    assert "você saiu" in await lab.tap(f"couple_end:{link_id}")
    assert await lab.scalar(select(PartnerLink.status).where(PartnerLink.id == link_id)) == "ended"
    assert await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.shared.is_(True))
    ) in (0, None)
    assert "ainda não divide" in await lab2.say("gastos da casa")


# ── privacy ───────────────────────────────────────────────────────────────────


@db
async def test_erasing_a_member_returns_the_partners_rows_to_private(lab: Lab, lab2: Lab) -> None:
    link_id = await _link(lab, lab2)
    await lab2.add(_exp(lab2, 20, shared=True))
    await lab.say("meu dashboard")
    await lab.tap(f"wipe:{int(time.time())}")
    assert await lab.scalar(select(PartnerLink).where(PartnerLink.id == link_id)) is None
    assert (
        await lab.scalar(select(Expense.shared).where(Expense.member_id == lab2.member_id)) is False
    )
    assert "ainda não divide" in await lab2.say("gastos da casa")


@db
async def test_export_includes_the_link_for_both_sides(lab: Lab, lab2: Lab) -> None:
    link_id = await _link(lab, lab2)
    for who in (lab, lab2):
        async with AsyncSessionLocal() as s:
            data = await export_member_data(s, await s.get(Member, who.member_id))
        ids = [r["id"] for r in data["tables"]["partner_link"]]
        assert str(link_id) in ids


@db
async def test_audit_trail_has_no_message_content(lab: Lab, lab2: Lab) -> None:
    code = await _code(lab)
    await lab2.say(f"entrar casa {code}")
    events = await lab.rows(
        select(AuditLog.event, AuditLog.detail).where(
            AuditLog.member_id.in_([lab.member_id, lab2.member_id])
        )
    )
    names = {e[0] for e in events}
    assert {"couple_invited", "couple_join_requested"} <= names
    assert code not in str([e[1] for e in events])


# ── panel ─────────────────────────────────────────────────────────────────────


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        return str(m.dashboard_token)


@db
async def test_home_tab_only_for_couples_and_only_shared_rows(
    lab: Lab, lab2: Lab, client, today
) -> None:
    tok, tok2 = await _token(lab.member_id), await _token(lab2.member_id)
    page = await client.get(f"/d/{tok}")
    assert 'data-tab="home"' not in page.text
    empty = (await client.get(f"/api/d/{tok}/home")).json()
    assert empty["tab"] == "home" and empty["cards"] == []

    await _link(lab, lab2)
    await lab.add(_exp(lab, 100, shared=True, merchant="Jumbo"), _exp(lab, 77, merchant="SEGREDO"))
    await lab2.add(_exp(lab2, 40, shared=True, merchant="Etos"))
    assert 'data-tab="home"' in (await client.get(f"/d/{tok}")).text
    body = (await client.get(f"/api/d/{tok}/home")).json()
    assert [c["id"] for c in body["cards"]] == ["home_balance", "home_entries"]
    v = body["cards"][0]["values"]
    assert (v["month_total"], v["paid_me"], v["paid_partner"], v["balance"]) == (
        140.0,
        100.0,
        40.0,
        30.0,
    )
    items = body["cards"][1]["items"]
    assert {i["merchant"] for i in items} == {"Jumbo", "Etos"}
    assert {i["who"] for i in items} == {"me", "partner"}
    assert "SEGREDO" not in str(body)
    other = (await client.get(f"/api/d/{tok2}/home")).json()
    assert other["cards"][0]["values"]["balance"] == -30.0
    assert "SEGREDO" not in str(other)


# ── i18n ──────────────────────────────────────────────────────────────────────


@db
@pytest.mark.parametrize("lang", ["en", "nl", "fr", "de"])
def test_every_couple_text_exists_in_five_languages(lang: str) -> None:
    for key, texts in couple.STRINGS.items():
        assert set(texts) == {"pt", "nl", "en", "fr", "de"}, key
        assert texts[lang], key
        # placeholders match across languages
        names = set(re.findall(r"\{(\w+)\}", str(texts["pt"])))
        assert set(re.findall(r"\{(\w+)\}", str(texts[lang]))) == names, (key, lang)
