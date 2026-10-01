"""V2-15 — days in the black, month vs month by category, and who owes whom."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from alfred.analysis import add_months
from alfred.clock import local_tz, today_local
from alfred.db import AsyncSessionLocal
from alfred.insights import blue_days, count_blue
from alfred.iou import MAX_OPEN, remaining
from alfred.models import Expense, Iou, Member
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 15)
FIRST = date(2026, 10, 1)


def _d(n: int) -> date:
    return date(2026, 10, n)


# ── days in the black: pure ───────────────────────────────────────────────────


def test_blue_days_income_in_the_middle_of_the_month() -> None:
    net = {_d(2): Decimal(-50), _d(5): Decimal(-30), _d(8): Decimal(100), _d(12): Decimal(-90)}
    r = count_blue(net, FIRST, TODAY)
    # day 1 = 0 (blue); 2-7 negative; 8-11 = +20 (blue); 12-15 = -70
    assert (r.blue, r.elapsed, r.longest, r.balance) == (1 + 4, 15, 4, Decimal(-70))


def test_blue_days_all_negative_after_first_spend_and_empty_start() -> None:
    r = count_blue({_d(3): Decimal(-1)}, FIRST, TODAY)
    assert (r.blue, r.longest) == (2, 2)  # days 1-2 are at zero


def test_blue_days_longest_streak_is_not_the_last_one() -> None:
    net = {_d(1): Decimal(10), _d(6): Decimal(-20), _d(7): Decimal(15), _d(9): Decimal(-1)}
    r = count_blue(net, FIRST, _d(10))
    assert r.longest == 5  # days 1-5; the later run (7-10) is shorter
    assert (r.blue, r.balance) == (5 + 4, Decimal(4))


# ── days in the black / month vs month: DB ────────────────────────────────────


def _at(day: date, hour: int = 12) -> datetime:
    return datetime.combine(day, time(hour), tzinfo=local_tz())


def _exp(lab: Lab, day: date, amount: float, category="restaurant", kind="expense") -> Expense:
    return Expense(
        id=uuid.uuid4(),
        member_id=lab.member_id,
        household_id=lab.household_id,
        transaction_type=kind,
        amount=amount,
        merchant="x",
        category=category,
        expense_date=_at(day),
    )


@db
async def test_blue_days_from_the_database(lab: Lab) -> None:
    await lab.add(
        _exp(lab, _d(2), 50),
        _exp(lab, _d(8), 100, "inkomen", "income"),
        _exp(lab, date(2026, 9, 30), 999),  # last month never counts
        _exp(lab, _d(16), 5),  # after "today": never counts
    )
    async with AsyncSessionLocal() as s:
        r = await blue_days(s, lab.member_id, TODAY)
        empty = await blue_days(s, uuid.uuid4(), TODAY)
    assert (r.blue, r.elapsed, r.balance) == (1 + 8, 15, Decimal(50))  # day 1, then 8..15
    assert empty is None


@db
async def test_blue_days_command(lab: Lab) -> None:
    assert "Ainda não há lançamentos" in await lab.say("dias no azul")
    await lab.add(_exp(lab, today_local(), 10))
    assert "dias no azul" in await lab.say("quantos dias no azul")


@db
async def test_month_vs_month_with_rises_and_new_categories(lab: Lab) -> None:
    today = today_local()
    prev_first = add_months(today.replace(day=1), -1)
    await lab.add(
        _exp(lab, today, 100, "restaurant"),
        _exp(lab, today, 60, "transport"),
        _exp(lab, today, 30, "kleding"),
        _exp(lab, today, 5, "overig"),
        _exp(lab, prev_first, 40, "restaurant"),
        _exp(lab, prev_first, 55, "transport"),
        _exp(lab, prev_first, 40, "overig"),
    )
    reply = await lab.say("mês contra mês por categoria")
    assert "Restaurante: €100,00 · €40,00 (+150%)" in reply
    assert "Kleding" in reply or "Vestuário" in reply
    assert "(novo)" in reply  # clothing had nothing last month
    rises = reply.splitlines()[-1]
    assert rises.startswith("Maiores altas:") and rises.count("(+") == 2
    assert "Restaurante" in rises  # the biggest rise (+60)
    assert "Outros" not in rises  # a drop is never a rise


@db
async def test_month_vs_month_empty_and_other_phrases(lab: Lab) -> None:
    assert "Ainda não há despesas" in await lab.say("compara por categoria")
    for phrase in (
        "month over month",
        "maand tegen maand",
        "mois contre mois",
        "monat gegen monat",
    ):
        assert phrase and await lab.say(phrase)  # answered (not the generic fallback)


@db
async def test_the_plain_comparison_is_untouched(lab: Lab) -> None:
    reply = await lab.say("compara este mês com o mês passado")
    assert "contra o mesmo período" not in reply


# ── who owes whom ─────────────────────────────────────────────────────────────


async def _ious(lab: Lab) -> list[Iou]:
    return [
        r[0]
        for r in await lab.rows(
            select(Iou).where(Iou.member_id == lab.member_id).order_by(Iou.created_at, Iou.id)
        )
    ]


@db
async def test_create_both_directions_and_list(lab: Lab) -> None:
    assert "Pedro te deve €25,00" in await lab.say("Pedro me deve 25")
    assert "Total do Pedro: €35,00" in await lab.say("pedro me deve 10 do almoço")
    assert "você deve €40,00 ao Lucas" in await lab.say("devo 40 pro Lucas")
    assert "você deve €5,00 ao Ana" in await lab.say("devo ao Ana 5")
    owed = await lab.say("quem me deve")
    assert "Te devem €35,00" in owed and "• Pedro: €25,00" in owed and "· almoço" in owed
    owe = await lab.say("o que eu devo")
    assert "Você deve €45,00" in owe and "• Lucas: €40,00" in owe
    rows = await _ious(lab)
    assert [(r.person, r.direction) for r in rows] == [
        ("Pedro", "owed_to_me"),
        ("Pedro", "owed_to_me"),
        ("Lucas", "i_owe"),
        ("Ana", "i_owe"),
    ]


@db
async def test_empty_lists(lab: Lab) -> None:
    assert "Ninguém te deve" in await lab.say("quem me deve")
    assert "não deve nada" in await lab.say("o que eu devo")


@db
async def test_settle_all_partial_exact_and_over(lab: Lab) -> None:
    await lab.say("Pedro me deve 35")
    assert "Anotei €10,00 de Pedro. Ainda em aberto: €25,00" in await lab.say("Pedro pagou 10")
    assert "• Pedro: €25,00 (de €35,00)" in await lab.say("quem me deve")
    assert "só tem €25,00 em aberto" in await lab.say("Pedro pagou 100")
    assert "Quitado" in await lab.say("Pedro pagou")
    assert all(r.settled_at for r in await _ious(lab))
    assert "Ninguém te deve" in await lab.say("quem me deve")


@db
async def test_same_person_with_two_open_amounts(lab: Lab) -> None:
    await lab.say("Pedro me deve 25")
    await lab.say("Pedro me deve 10")
    ask = await lab.say("Pedro pagou")
    assert "mais de um valor" in ask and "€25,00" in ask and "€10,00" in ask
    assert not any(r.settled_at for r in await _ious(lab))
    await lab.say("Pedro pagou 10")  # equals one item's balance: settles exactly that one
    rows = await _ious(lab)
    assert [bool(r.settled_at) for r in rows] == [False, True]
    assert remaining(rows[0]) == Decimal("25.00")


@db
async def test_payment_larger_than_one_item_spills_to_the_next(lab: Lab) -> None:
    await lab.say("Pedro me deve 25")
    await lab.say("Pedro me deve 10")
    await lab.say("Pedro pagou 30")
    rows = await _ious(lab)
    assert [bool(r.settled_at) for r in rows] == [True, False]
    assert remaining(rows[1]) == Decimal("5.00")


@db
async def test_repeated_first_names_are_disambiguated(lab: Lab) -> None:
    await lab.say("Pedro Silva me deve 5")
    await lab.say("Pedro Costa me deve 7")
    ask = await lab.say("Pedro pagou")
    assert "Quem exatamente" in ask and "Pedro Costa, Pedro Silva" in ask
    assert "Quitado" in await lab.say("Pedro Silva pagou")
    assert "Quitado" in await lab.say("Pedro pagou")  # only one Pedro left
    await lab.say("Pedro me deve 1")
    await lab.say("Pedro Silva me deve 2")
    assert "Quitado: Pedro pagou" in await lab.say(
        "Pedro pagou"
    )  # the exact name wins over "Pedro Silva"


@db
async def test_i_paid_and_the_fixed_bills_handler_still_gets_its_message(lab: Lab) -> None:
    await lab.say("devo 40 pro Lucas")
    assert "Quitado" in await lab.say("paguei o Lucas")
    await lab.say("aluguel 1200 todo dia 1")
    reply = await lab.say("paguei o aluguel")
    assert "€1.200,00" in reply and "Quitado" not in reply
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 1
    )


@db
async def test_invalid_amounts_and_ordinary_messages(lab: Lab) -> None:
    assert "Não entendi o valor" in await lab.say("Pedro me deve 0")
    assert "Não entendi o valor" in await lab.say("Pedro me deve 2000000")
    assert await lab.say("gastei 25 com o Pedro") == "[llm]"
    assert await lab.say("quem pagou a conta?") == "[llm]"
    assert await _ious(lab) == []


@db
async def test_limit_of_open_amounts(lab: Lab) -> None:
    await lab.add(
        *[
            Iou(member_id=lab.member_id, person=f"P{i}", amount=1, direction="owed_to_me")
            for i in range(MAX_OPEN)
        ]
    )
    assert str(MAX_OPEN) in await lab.say("Pedro me deve 25")


@db
async def test_reminder_text_is_ready_to_copy(lab: Lab) -> None:
    assert await lab.say("cobra o João") == "[llm]"  # nobody by that name: not ours
    await lab.say("Pedro me deve 25")
    reply = await lab.say("cobra o Pedro")
    assert "Oi Pedro!" in reply and "€25,00" in reply and "Tikkie" in reply


@db
@pytest.mark.parametrize(
    ("lang", "owed", "who", "owe", "header"),
    [
        ("pt", "Pedro me deve 25", "quem me deve", "devo 40 pro Lucas", "Te devem €25,00"),
        (
            "nl",
            "Pedro is mij 25 schuldig",
            "wie is mij geld schuldig",
            "ik ben Lucas 40 schuldig",
            "Je krijgt nog €25,00",
        ),
        ("en", "Pedro owes me 25", "who owes me", "I owe Lucas 40", "You're owed €25,00"),
        ("fr", "Pedro me doit 25", "qui me doit", "je dois 40 à Lucas", "On te doit €25,00"),
        (
            "de",
            "Pedro schuldet mir 25",
            "wer schuldet mir",
            "ich schulde Lucas 40",
            "Du bekommst noch €25,00",
        ),
    ],
)
async def test_five_languages(lab: Lab, lang, owed, who, owe, header) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.language = lang
        await s.commit()
    for text in (owed, owe):
        reply = await lab.say(text)
        assert "{" not in reply and "€" in reply
    assert header in await lab.say(who)
    assert "Pedro" in (await _ious(lab))[0].person
    assert [r.direction for r in await _ious(lab)] == ["owed_to_me", "i_owe"]


@db
async def test_private_exported_and_erased(lab: Lab) -> None:
    await lab.say("Pedro me deve 25")
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        data = await export_member_data(s, m)
        assert [r["person"] for r in data["tables"]["iou"]] == ["Pedro"]
    other_phone = "3162" + uuid.uuid4().hex[:7]
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
        async with AsyncSessionLocal() as s:
            # another member of the household sees nothing of Pedro
            assert (
                await s.scalar(
                    select(func.count()).select_from(Iou).where(Iou.member_id == other_id)
                )
                == 0
            )
        async with AsyncSessionLocal() as s:
            m = await s.get(Member, other_id)
            await erase_member(s, m)
            await s.commit()
        assert len(await _ious(lab)) == 1  # erasing someone else touched nothing
    finally:
        pass
