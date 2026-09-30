"""V1-21 — LLM token accounting per member, cost and D7/D30 retention metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from pydantic import SecretStr
from sqlalchemy import func, select

from alfred import llm_usage
from alfred.db import AsyncSessionLocal, engine
from alfred.internal import llm_cost_eur, retention
from alfred.models import LlmUsage, Member, Message
from alfred.privacy import erase_member
from alfred.settings import settings

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _resp(i: int, o: int) -> SimpleNamespace:
    return SimpleNamespace(usage=SimpleNamespace(input_tokens=i, output_tokens=o))


def test_retention_counts_only_members_old_enough() -> None:
    d = timedelta(days=1)
    pairs = [
        (NOW - 10 * d, NOW - 1 * d),  # came back after day 7 → retained
        (NOW - 10 * d, NOW - 9 * d),  # never came back → eligible, not retained
        (NOW - 3 * d, NOW),  # too new for D7
    ]
    r7 = retention(pairs, NOW, 7)
    assert r7 == {"eligible": 2, "retained": 1, "rate": 0.5}
    assert retention(pairs, NOW, 30) == {"eligible": 0, "retained": 0, "rate": None}


def test_cost_uses_configured_prices(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_input_usd_per_mtok", 1.0)
    monkeypatch.setattr(settings, "llm_output_usd_per_mtok", 5.0)
    monkeypatch.setattr(settings, "usd_to_eur", 1.0)
    assert llm_cost_eur(1_000_000, 200_000) == 2.0
    assert llm_cost_eur(0, 0) == 0.0


def test_record_is_a_noop_outside_a_capture_and_never_raises() -> None:
    llm_usage._buffer.set(None)
    llm_usage.record("expense", "m", _resp(1, 2))  # no capture → nothing happens
    buf = llm_usage.begin()
    llm_usage.record("expense", "m", object())  # odd response → ignored, no exception
    llm_usage.record("expense", "model-x", _resp(10, 5))
    assert buf == [
        {"purpose": "expense", "model": "model-x", "input_tokens": 10, "output_tokens": 5}
    ]
    llm_usage._buffer.set(None)


async def test_flush_writes_rows_and_erase_removes_them(lab) -> None:
    await engine.dispose()
    buf = llm_usage.begin()
    llm_usage.record("multi", "m", _resp(100, 20))
    llm_usage.record("reply", "m", _resp(300, 40))
    async with AsyncSessionLocal() as s:
        assert await llm_usage.flush(s, lab.member_id, buf) == 2
        await s.commit()
        n = await s.scalar(
            select(func.count()).select_from(LlmUsage).where(LlmUsage.member_id == lab.member_id)
        )
        assert n == 2
        member = await s.get(Member, lab.member_id)
        await erase_member(s, member)
        await s.commit()
    async with AsyncSessionLocal() as s:
        left = await s.scalar(
            select(func.count()).select_from(LlmUsage).where(LlmUsage.member_id == lab.member_id)
        )
        assert left == 0
    await engine.dispose()


async def test_flush_survives_a_missing_member(lab) -> None:
    import uuid

    await engine.dispose()
    buf = llm_usage.begin()
    llm_usage.record("reply", "m", _resp(1, 1))
    async with AsyncSessionLocal() as s:
        assert await llm_usage.flush(s, uuid.uuid4(), buf) == 0  # FK fails inside SAVEPOINT
        await s.commit()  # the surrounding transaction is still usable
    await engine.dispose()


async def test_metrics_endpoint_reports_llm_and_retention(lab, client, monkeypatch) -> None:
    await engine.dispose()
    monkeypatch.setattr(settings, "internal_metrics_token", SecretStr("s3cret"))
    monkeypatch.setattr(settings, "usd_to_eur", 1.0)
    async with AsyncSessionLocal() as s:
        now = datetime.now(UTC)
        for days_ago, n in ((20, "a"), (2, "b")):
            s.add(
                Message(
                    wa_message_id=f"wamid.ret.{lab.member_id}.{n}",
                    household_id=lab.household_id,
                    author_id=lab.member_id,
                    direction="inbound",
                    body="oi",
                    created_at=now - timedelta(days=days_ago),
                )
            )
        s.add(
            LlmUsage(
                member_id=lab.member_id,
                purpose="reply",
                model="m",
                input_tokens=1_000_000,
                output_tokens=0,
            )
        )
        await s.commit()
    await engine.dispose()
    r = await client.get("/internal/metrics", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    data = r.json()
    assert data["llm_30d"]["calls"] >= 1
    assert data["llm_30d"]["estimated_cost_eur"] >= 1.0
    assert data["llm_30d"]["members_with_calls"] >= 1
    assert data["retention"]["d7"]["eligible"] >= 1 and data["retention"]["d7"]["retained"] >= 1
    await engine.dispose()


async def test_handle_one_stores_tokens_spent_by_the_handler(lab, monkeypatch) -> None:
    from alfred import webhook

    await engine.dispose()

    async def fake_handler(member, stored, session) -> None:
        llm_usage.record("expense", "m", _resp(50, 10))

    monkeypatch.setattr("alfred.conversation.handle_inbound", fake_handler)
    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        stored = Message(
            wa_message_id=f"wamid.usage.{lab.member_id}",
            household_id=lab.household_id,
            author_id=lab.member_id,
            direction="inbound",
            body="x",
        )
        s.add(stored)
        await s.flush()
        assert await webhook._handle_one(
            member, stored, s, SimpleNamespace(error=lambda *a, **k: 0)
        )
    async with AsyncSessionLocal() as s:
        row = (
            await s.execute(select(LlmUsage).where(LlmUsage.member_id == lab.member_id))
        ).scalar_one()
        assert (row.purpose, row.input_tokens, row.output_tokens) == ("expense", 50, 10)
    await engine.dispose()
