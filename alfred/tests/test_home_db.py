# ruff: noqa: E501
"""V2-27 — home contracts (energy, gas, internet): end date, reminders, chat and photo."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from alfred.db import AsyncSessionLocal
from alfred.home import contract_from_reading, due_reminders, mark_sent, parse_contract
from alfred.models import HomeContract, Message
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)
PHOTO = b"\xff\xd8\xffcontract"


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.conversation.today_local", lambda: TODAY)


async def _rows(lab: Lab) -> list[HomeContract]:
    rows = await lab.rows(
        select(HomeContract)
        .where(HomeContract.member_id == lab.member_id)
        .order_by(HomeContract.created_at)
    )
    return [r[0] for r in rows]


def C(**kw):
    base = {
        "kind": "home_contract",
        "contract_type": "energy",
        "provider": "Vattenfall",
        "monthly_amount": 120,
        "currency": "EUR",
        "end_date": "2027-12-31",
    }
    base.update(kw)
    return base


# ── pure rules ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "kind", "provider", "amount", "end"),
    [
        (
            "contrato energia Vattenfall 120 por mês até 31/12/2027",
            "energy",
            "Vattenfall",
            120.0,
            date(2027, 12, 31),
        ),
        ("contrato de luz Eneco até 01/03/2027", "energy", "Eneco", None, date(2027, 3, 1)),
        (
            "contract gas Essent €85,50 per maand tot 15/11/2027",
            "gas",
            "Essent",
            85.5,
            date(2027, 11, 15),
        ),
        ("contract internet Ziggo 55 until 05/01", "internet", "Ziggo", 55.0, date(2027, 1, 5)),
        ("contrat internet Free 30 jusqu'au 02/02/28", "internet", "Free", 30.0, date(2028, 2, 2)),
        ("Vertrag Strom E.ON 99 bis 30/06/2027", "energy", "E.ON", 99.0, date(2027, 6, 30)),
    ],
)
def test_text_is_parsed(text, kind, provider, amount, end) -> None:
    got = parse_contract(text, TODAY)
    assert got == {"kind": kind, "provider": provider, "amount": amount, "ends_on": end}


@pytest.mark.parametrize(
    "text",
    [
        "contrato energia Vattenfall 120",  # no end date
        "contrato energia 120 até 31/12/2027",  # no provider
        "contrato energia Vattenfall 120 até 31/12/2020",  # past
        "contrato energia Vattenfall 120 até 31/12/2050",  # too far
        "contrato energia Vattenfall 120 até 31/02/2027",  # not a date
        "contrato seguro Allianz 50 até 31/12/2027",  # unsupported kind
        "contrato energia Vattenfall 0 até 31/12/2027",
    ],
)
def test_bad_text_is_refused(text) -> None:
    assert parse_contract(text, TODAY) is None


def test_reading_is_untrusted() -> None:
    f = contract_from_reading(C(provider="  *Vattenfall*\n" + "x" * 200), TODAY)
    assert f is not None and "*" not in f["provider"] and len(f["provider"]) <= 60
    assert contract_from_reading(C(contract_type="phone"), TODAY) is None
    assert contract_from_reading(C(provider=None), TODAY) is None
    assert contract_from_reading(C(end_date="2020-01-01"), TODAY) is None
    assert contract_from_reading(C(end_date=None), TODAY) is None
    assert contract_from_reading(C(currency="USD"), TODAY)["amount"] is None
    assert contract_from_reading(C(monthly_amount=-3), TODAY)["amount"] == 3.0


# ── chat ──────────────────────────────────────────────────────────────────────


@db
async def test_text_previews_then_saves_on_confirm(lab: Lab) -> None:
    out = await lab.say("contrato energia Vattenfall 120 por mês até 31/12/2027")
    assert "Vattenfall" in out and "31/12/2027" in out
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["home_ok", "home_no"]
    (draft,) = await _rows(lab)
    assert draft.status == "draft"
    await lab.tap(lab.buttons[-1][0][0])
    (rec,) = await _rows(lab)
    assert rec.status == "active" and rec.ends_on == date(2027, 12, 31) and rec.kind == "energy"
    await lab.tap(lab.buttons[-1][0][0]) if lab.buttons[-1] else None
    assert len(await _rows(lab)) == 1


@db
async def test_cancel_and_replace_draft(lab: Lab) -> None:
    await lab.say("contrato energia A 10 até 31/12/2027")
    await lab.say("contrato energia B 20 até 31/12/2027")
    (rec,) = await _rows(lab)
    assert rec.provider == "B"
    await lab.tap(lab.buttons[-1][-1][0])
    assert await _rows(lab) == []


@db
async def test_expired_draft_is_refused(lab: Lab) -> None:
    await lab.say("contrato energia A 10 até 31/12/2027")
    async with AsyncSessionLocal() as s:
        rec = (await s.execute(select(HomeContract))).scalars().first()
        rec.created_at = datetime.now(UTC) - timedelta(minutes=30)
        await s.commit()
    await lab.tap(lab.buttons[-1][0][0])
    assert await _rows(lab) == []


@db
async def test_photo_flows_into_a_preview(lab: Lab) -> None:
    out = await lab.media("image", PHOTO, C())
    assert "Vattenfall" in out and "31/12/2027" in out
    (draft,) = await _rows(lab)
    assert draft.status == "draft"
    out = await lab.media("image", PHOTO, C(end_date=None))
    assert "contrato" in out.lower()


@db
async def test_one_contract_per_kind_a_new_one_replaces_the_old_on_confirm(lab: Lab) -> None:
    for provider in ("Old", "New"):
        await lab.say(f"contrato energia {provider} 10 até 31/12/2027")
        await lab.tap(lab.buttons[-1][0][0])
    rows = await _rows(lab)
    assert [r.provider for r in rows] == ["New"]


@db
async def test_list_renew_delete(lab: Lab) -> None:
    for text in (
        "contrato energia Vattenfall 120 até 31/12/2027",
        "contrato internet Ziggo 55 até 05/01/2027",
    ):
        await lab.say(text)
        await lab.tap(lab.buttons[-1][0][0])
    out = await lab.say("meus contratos")
    assert "Vattenfall" in out and "Ziggo" in out and "83" in out  # days to 05/01/2027
    assert out.index("Ziggo") < out.index("Vattenfall")  # soonest first
    out = await lab.say("renovei contrato internet até 05/01/2029")
    assert "05/01/2029" in out
    (zig,) = [r for r in await _rows(lab) if r.kind == "internet"]
    assert zig.ends_on == date(2029, 1, 5) and not zig.sent_60 and not zig.sent_30
    await lab.say("apaga contrato energia")
    assert [r.kind for r in await _rows(lab)] == ["internet"]
    assert "Ziggo" in await lab.say("contratos")


@db
async def test_empty_list_and_unrelated_text(lab: Lab) -> None:
    out = await lab.say("contratos")
    assert "contrato energia" in out.lower()
    assert await lab.say("o contrato de aluguel vence amanhã?") == "[llm]"


@db
async def test_bad_text_gets_an_example(lab: Lab) -> None:
    out = await lab.say("contrato energia Vattenfall 120")
    assert "até" in out and await _rows(lab) == []


@db
async def test_limit(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        for i in range(20):
            s.add(
                HomeContract(
                    member_id=lab.member_id,
                    kind="energy",
                    provider=f"p{i}",
                    ends_on=TODAY + timedelta(days=100),
                    status="active",
                )
            )
        await s.commit()
    out = await lab.say("contrato gas X 10 até 31/12/2027")
    assert "20" in out


@db
async def test_export_and_erase_cover_contracts(lab: Lab) -> None:
    await lab.say("contrato energia Vattenfall 120 até 31/12/2027")
    await lab.tap(lab.buttons[-1][0][0])
    async with AsyncSessionLocal() as s:
        from alfred.models import Member

        member = await s.get(Member, lab.member_id)
        assert "Vattenfall" in str(await export_member_data(s, member))
        await erase_member(s, member)
        await s.commit()
    assert await lab.scalar(select(func.count()).select_from(HomeContract)) == 0


# ── reminders ─────────────────────────────────────────────────────────────────


async def _active(lab: Lab, days: int, **kw) -> HomeContract:
    async with AsyncSessionLocal() as s:
        rec = HomeContract(
            member_id=lab.member_id,
            kind="energy",
            provider="Vattenfall",
            ends_on=TODAY + timedelta(days=days),
            status="active",
            **kw,
        )
        s.add(rec)
        await s.commit()
        return rec


@db
async def test_60_then_30_once_each(lab: Lab) -> None:
    rec = await _active(lab, 59)
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.record_id == rec.id and r.which == "d60" and "Vattenfall" in r.text
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY, datetime.now(UTC)) == []
        (r,) = await due_reminders(s, TODAY + timedelta(days=30), datetime.now(UTC))
        assert r.which == "d30"
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY + timedelta(days=40), datetime.now(UTC)) == []


@db
async def test_a_late_registration_sends_only_the_urgent_one(lab: Lab) -> None:
    await _active(lab, 20)
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.which == "d30"
        await mark_sent(s, r.record_id, r.which)
        await s.commit()
        assert await due_reminders(s, TODAY, datetime.now(UTC)) == []


@db
async def test_ended_far_and_draft_contracts_are_ignored(lab: Lab) -> None:
    await _active(lab, -1)
    await _active(lab, 200)
    async with AsyncSessionLocal() as s:
        s.add(
            HomeContract(
                member_id=lab.member_id,
                kind="gas",
                provider="D",
                ends_on=TODAY + timedelta(days=10),
                status="draft",
            )
        )
        await s.commit()
        assert await due_reminders(s, TODAY, datetime.now(UTC)) == []


@db
async def test_in_window_flag_follows_last_inbound(lab: Lab) -> None:
    await _active(lab, 10)
    async with AsyncSessionLocal() as s:
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.in_window is False
        s.add(
            Message(
                wa_message_id=uuid.uuid4().hex,
                household_id=lab.household_id,
                author_id=lab.member_id,
                direction="inbound",
                body="oi",
            )
        )
        await s.commit()
        (r,) = await due_reminders(s, TODAY, datetime.now(UTC))
        assert r.in_window is True
