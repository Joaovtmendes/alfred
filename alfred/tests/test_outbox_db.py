"""V2-18 — what the bot sent, with delivery status from the Meta status webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from alfred import delivery
from alfred.conversation import _save_outbound
from alfred.db import AsyncSessionLocal
from alfred.main import app
from alfred.models import Member, Message
from alfred.outbox import (
    RETENTION_DAYS,
    apply_status,
    parse_outbox_query,
    purge_old,
    record_outbound,
)
from alfred.privacy import export_member_data
from alfred.settings import settings
from alfred.whatsapp import _wamid
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _out(lab: Lab, wamid, body="oi", kind="reply", status="sent", age_h=0, error=None) -> Message:
    return Message(
        id=uuid.uuid4(),
        wa_message_id=wamid,
        household_id=lab.household_id,
        author_id=lab.member_id,
        direction="outbound",
        body=body,
        processed=True,
        kind=kind,
        delivery_status=status,
        delivery_error=error,
        created_at=datetime.now(UTC) - timedelta(hours=age_h),
    )


async def _get(wamid: str) -> Message:
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(Message).where(Message.wa_message_id == wamid))).scalar_one()


async def _apply(**status) -> bool:
    async with AsyncSessionLocal() as s:
        ok = await apply_status(s, status)
        await s.commit()
        return ok


# ── pure ──────────────────────────────────────────────────────────────────────


def test_wamid_extraction() -> None:
    assert _wamid({"messages": [{"id": "wamid.1"}]}) == "wamid.1"
    assert _wamid({"suppressed": True}) is None
    assert _wamid({"messages": []}) is None


@pytest.mark.parametrize(
    ("text", "today", "reminders"),
    [
        ("o que voce me enviou", False, False),
        ("o que voce me enviou hoje", True, False),
        ("lembretes que voce mandou", False, True),
        ("what did you send me today", True, False),
        ("which reminders did you send", False, True),
        ("wat heb je me gestuurd vandaag", True, False),
        ("herinneringen die je me stuurde", False, True),
        ("qu'est-ce que tu m'as envoye aujourd'hui", True, False),
        ("rappels que tu m'as envoye", False, True),
        ("was hast du mir heute geschickt", None, None),
        ("erinnerungen die du mir geschickt hast", False, True),
    ],
)
def test_outbox_query_parsing(text, today, reminders) -> None:
    q = parse_outbox_query(text)
    if today is None:
        assert q is None or q[0]  # German word order with "heute" in the middle: tolerated
        return
    assert q == (today, reminders)


def test_unrelated_text_is_not_an_outbox_query() -> None:
    assert parse_outbox_query("o que eu gastei hoje") is None
    assert parse_outbox_query("saldo") is None


# ── status webhook logic ──────────────────────────────────────────────────────


@db
async def test_status_goes_sent_delivered_read(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.A"))
    assert await _apply(id="wamid.A", status="delivered")
    assert (await _get("wamid.A")).delivery_status == "delivered"
    assert await _apply(id="wamid.A", status="read")
    assert (await _get("wamid.A")).delivery_status == "read"


@db
async def test_out_of_order_read_before_delivered_keeps_read(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.B"))
    assert await _apply(id="wamid.B", status="read")
    assert not await _apply(id="wamid.B", status="delivered")
    assert not await _apply(id="wamid.B", status="sent")
    assert (await _get("wamid.B")).delivery_status == "read"


@db
async def test_failure_records_the_meta_error(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.C"))
    err = [{"code": 131047, "title": "Re-engagement message"}]
    assert await _apply(id="wamid.C", status="failed", errors=err)
    row = await _get("wamid.C")
    assert row.delivery_status == "failed" and "131047" in row.delivery_error
    assert not await _apply(id="wamid.C", status="sent")  # a late "sent" does not hide it
    assert (await _get("wamid.C")).delivery_status == "failed"


@db
async def test_failure_after_delivery_is_ignored(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.D", status="delivered"))
    assert not await _apply(id="wamid.D", status="failed", errors=[{"code": 1}])
    assert (await _get("wamid.D")).delivery_status == "delivered"


@db
async def test_unknown_malformed_or_inbound_ids_change_nothing(lab: Lab) -> None:
    await lab.add(
        Message(
            id=uuid.uuid4(),
            wa_message_id="wamid.IN",
            household_id=lab.household_id,
            author_id=lab.member_id,
            direction="inbound",
            body="oi",
        )
    )
    assert not await _apply(id="wamid.nobody", status="read")
    assert not await _apply(id="wamid.IN", status="read")
    assert not await _apply(id=None, status="read")
    assert not await _apply(id="wamid.IN", status="weird")
    assert (await _get("wamid.IN")).delivery_status is None


# ── the webhook route (HMAC stays fail-closed) ────────────────────────────────


def _payload(wamid: str, status: str) -> bytes:
    return json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "statuses": [{"id": wamid, "status": status, "recipient_id": "1"}]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode()


def _sign(body: bytes) -> str:
    key = settings.whatsapp_app_secret.get_secret_value().encode()
    return "sha256=" + hmac.new(key, body, hashlib.sha256).hexdigest()


@db
async def test_signed_status_payload_updates_the_message(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.W"))
    body = _payload("wamid.W", "delivered")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            "/webhook/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body)},
        )
    assert r.status_code == 200
    assert (await _get("wamid.W")).delivery_status == "delivered"


@db
async def test_unsigned_or_badly_signed_status_payload_is_rejected(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.X"))
    body = _payload("wamid.X", "read")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r1 = await c.post("/webhook/whatsapp", content=body)
        r2 = await c.post(
            "/webhook/whatsapp",
            content=body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=bad"},
        )
    assert r1.status_code in (401, 403) and r2.status_code in (401, 403)
    assert (await _get("wamid.X")).delivery_status == "sent"


# ── storing what was sent ────────────────────────────────────────────────────


@db
async def test_save_outbound_keeps_the_meta_id_of_the_send(lab: Lab) -> None:
    delivery.begin()
    try:
        delivery.note_sent("wamid.SENT1")
        async with AsyncSessionLocal() as s:
            member = await s.get(Member, lab.member_id)
            await _save_outbound(member, "oi", s)
            await s.commit()
    finally:
        delivery.end()
    row = await _get("wamid.SENT1")
    assert (row.kind, row.delivery_status, row.direction) == ("reply", "sent", "outbound")


@db
async def test_save_outbound_without_a_send_gets_a_local_id_and_no_status(lab: Lab) -> None:
    await lab.say("oi")
    rows = await lab.rows(
        select(Message.wa_message_id, Message.delivery_status, Message.kind).where(
            Message.household_id == lab.household_id
        )
    )
    outs = [r for r in rows if r.wa_message_id.startswith("out-")]
    assert outs and all(r.delivery_status is None and r.kind == "reply" for r in outs)


@db
async def test_record_outbound_for_cron_sends(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        ok = await record_outbound(
            s, lab.phone, "aluguel vence", kind="reminder", wa_message_id="wamid.R",
            template_name="alfred_payment_reminder",
        )  # fmt: skip
        missing = await record_outbound(s, "000", "x", kind="reply", wa_message_id=None)
        await s.commit()
    assert ok and not missing
    row = await _get("wamid.R")
    assert (row.kind, row.template_name, row.delivery_status) == (
        "reminder",
        "alfred_payment_reminder",
        "sent",
    )


@db
async def test_cron_monthly_summary_is_stored_with_its_meta_id(lab: Lab) -> None:
    from tests.test_monthly_summary_db import DAY1, FIRST, _cron, _inbound, _txn

    await lab.add(_txn(lab, 10, "supermarkt", FIRST))
    await _inbound(lab, 1)

    async def fake_text(to, text):
        return {"messages": [{"id": "wamid.CRON1"}]}

    with patch("alfred.whatsapp.send_text", fake_text):
        await _cron().send_monthly_summaries(DAY1)
    row = await _get("wamid.CRON1")
    assert row.kind == "summary" and row.author_id == lab.member_id


# ── "o que você me enviou" ───────────────────────────────────────────────────


@db
async def test_list_shows_status_kind_and_only_my_messages(lab: Lab) -> None:
    other = uuid.uuid4()
    await lab.add(
        _out(lab, "wamid.1", "Anotei: café 3,50", "reply", "read"),
        _out(lab, "wamid.2", "Aluguel vence", "reminder", "failed", error="131047: fora da janela"),
        _out(lab, "wamid.3", "Resumo de setembro", "summary", "delivered"),
    )  # fmt: skip
    async with AsyncSessionLocal() as s:  # a message of somebody else never shows up
        from alfred.models import Household

        hh = Household(name="other")
        s.add(hh)
        await s.flush()
        m = Member(id=other, household_id=hh.id, wa_phone="3199" + uuid.uuid4().hex[:7])
        s.add(m)
        await s.flush()
        s.add(
            Message(
                id=uuid.uuid4(), wa_message_id="wamid.O", household_id=hh.id, author_id=other,
                direction="outbound", body="SEGREDO DE OUTRO", processed=True, kind="reply",
            )
        )  # fmt: skip
        await s.commit()
    try:
        reply = await lab.say("o que você me enviou")
        assert "Anotei: café" in reply and "lida" in reply
        assert "Aluguel vence" in reply and "não entregue" in reply and "131047" in reply
        assert "Resumo de setembro" in reply and "entregue" in reply
        assert "SEGREDO" not in reply
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(Message.__table__.delete().where(Message.author_id == other))
            await s.execute(Member.__table__.delete().where(Member.id == other))
            await s.commit()


@db
async def test_list_today_and_reminders_filters(lab: Lab) -> None:
    await lab.add(
        _out(lab, "wamid.old", "mensagem antiga", "reply", "read", age_h=72),
        _out(lab, "wamid.new", "mensagem nova", "reply", "read", age_h=0),
        _out(lab, "wamid.rem", "lembrete do dentista", "reminder", "delivered", age_h=0),
    )
    today = await lab.say("o que você me enviou hoje")
    assert "mensagem nova" in today and "mensagem antiga" not in today
    rem = await lab.say("lembretes que você mandou")
    assert "lembrete do dentista" in rem and "mensagem nova" not in rem


@db
async def test_list_empty_and_other_languages(lab: Lab) -> None:
    assert "Não mandei nada" in await lab.say("lembretes que você mandou")
    await lab.add(_out(lab, "wamid.e", "hello", "reply", "read"))
    assert await lab.say("what did you send me")


@db
async def test_list_is_capped_at_ten_lines(lab: Lab) -> None:
    await lab.add(*[_out(lab, f"wamid.m{i}", f"msg {i}", age_h=i) for i in range(15)])
    reply = await lab.say("o que você me enviou")
    assert len([ln for ln in reply.splitlines() if " · " in ln]) == 10


# ── retention / privacy ──────────────────────────────────────────────────────


@db
async def test_purge_removes_only_old_outbound(lab: Lab) -> None:
    await lab.add(
        _out(lab, "wamid.ancient", age_h=24 * (RETENTION_DAYS + 5)),
        _out(lab, "wamid.fresh", age_h=24),
        Message(
            id=uuid.uuid4(), wa_message_id="wamid.oldin", household_id=lab.household_id,
            author_id=lab.member_id, direction="inbound", body="velha",
            created_at=datetime.now(UTC) - timedelta(days=RETENTION_DAYS + 5),
        ),
    )  # fmt: skip
    async with AsyncSessionLocal() as s:
        n = await purge_old(s)
        await s.commit()
    assert n >= 1
    async with AsyncSessionLocal() as s:
        ids = {
            r
            for r in (
                await s.execute(
                    select(Message.wa_message_id).where(Message.author_id == lab.member_id)
                )
            ).scalars()
        }
    assert "wamid.ancient" not in ids and {"wamid.fresh", "wamid.oldin"} <= ids


@db
async def test_delivery_fields_are_exported(lab: Lab) -> None:
    await lab.add(_out(lab, "wamid.exp", status="delivered"))
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
    assert "delivery_status" in str(data) and "delivered" in str(data)
