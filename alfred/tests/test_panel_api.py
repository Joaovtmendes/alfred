from __future__ import annotations

import os
import statistics
import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text

from alfred import panel_tokens
from alfred.db import AsyncSessionLocal
from alfred.models import AuditLog, Expense, HealthLog, Household, Member
from alfred.web_security import _dashboard_limiter

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")
TABS = ("summary", "money", "health", "agenda", "trips")


@pytest.fixture(autouse=True)
def _fresh_rate_limiter():
    """These tests make many requests from one IP; keep them from eating other tests' budget."""
    _dashboard_limiter._hits.clear()
    yield
    _dashboard_limiter._hits.clear()


async def _token(member_id) -> str:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, member_id)
        await panel_tokens.ensure_panel_token(s, m)
        await s.commit()
        t = str(m.dashboard_token)
    return t


async def _add(lab, amount, kind="expense", status="paid", day=1, **kw) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type=kind,
            amount=amount,
            merchant=kw.get("merchant", "x"),
            category=kw.get("category", "overig"),
            status=status,
            expense_date=datetime(2026, 10, day, 12, tzinfo=UTC),
        )
    )


def _balance(body: dict) -> dict:
    return next(c for c in body["cards"] if c["id"] == "balance")


@db
@pytest.mark.parametrize("tab", TABS)
async def test_unknown_or_malformed_token_is_404(lab, client, tab) -> None:
    assert (await client.get(f"/api/d/{uuid.uuid4()}/{tab}")).status_code == 404
    assert (await client.get(f"/api/d/not-a-uuid/{tab}")).status_code == 404


@db
@pytest.mark.parametrize("tab", TABS)
async def test_every_tab_answers_with_the_common_envelope(lab, client, tab) -> None:
    token = await _token(lab.member_id)
    r = await client.get(f"/api/d/{token}/{tab}?month=2026-10&kind=expense")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["tab"] == tab and body["lang"] == "pt"
    assert body["filter"] == {
        "start": "2026-10-01",
        "end": "2026-11-01",
        "categories": [],
        "kind": "expense",
        "states": [],
        "trip": None,
    }
    assert isinstance(body["cards"], list)
    if tab != "summary":
        assert body["cards"] == []  # content arrives with each tab's own PR


@db
async def test_expired_token_is_404(lab, client) -> None:
    token = await _token(lab.member_id)
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.dashboard_token_created_at = datetime(2026, 1, 1, tzinfo=UTC)
        await s.commit()
    assert (await client.get(f"/api/d/{token}/summary")).status_code == 404


@db
async def test_summary_balance_uses_settled_only_and_the_filter(lab, client) -> None:
    await _add(lab, 3840.0, kind="income", status="received", day=1)
    await _add(lab, 1000.0, day=2)
    await _add(lab, 997.70, day=3)
    await _add(lab, 500.0, status="to_pay", day=4)  # pending: never in the balance
    token = await _token(lab.member_id)
    body = (await client.get(f"/api/d/{token}/summary?month=2026-10")).json()
    card = _balance(body)
    assert card["values"] == {"income": 3840.0, "expense": 1997.7, "balance": 1842.3}
    assert card["empty"] is False
    only = (await client.get(f"/api/d/{token}/summary?month=2026-10&kind=expense")).json()
    c2 = _balance(only)
    assert c2["values"]["income"] == 0.0 and c2["values"]["expense"] == 1997.7
    # a pending-only filter leaves nothing settled to count
    pend = (await client.get(f"/api/d/{token}/summary?month=2026-10&state=to_pay")).json()
    assert _balance(pend)["empty"] is True


@db
async def test_empty_month_is_marked_empty_without_a_phrase(lab, client) -> None:
    token = await _token(lab.member_id)
    card = _balance((await client.get(f"/api/d/{token}/summary?month=2026-10")).json())
    assert card["empty"] is True and card["phrase"] is None
    assert card["values"] == {"income": 0.0, "expense": 0.0, "balance": 0.0}


@db
async def test_free_text_filters_are_ignored_by_the_server(lab, client) -> None:
    await _add(lab, 10.0, merchant="Albert Heijn")
    token = await _token(lab.member_id)
    a = (await client.get(f"/api/d/{token}/summary?month=2026-10")).json()
    b = (
        await client.get(f"/api/d/{token}/summary?month=2026-10&merchant=Zara&person=Marta")
    ).json()
    assert a["cards"] == b["cards"] and _balance(a)["values"]["expense"] == 10.0


@db
async def test_another_members_token_never_sees_my_data(lab, client) -> None:
    await _add(lab, 99.0)
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
        other_id, hh_id = other.id, hh.id
    try:
        token = await _token(other_id)
        body = (await client.get(f"/api/d/{token}/summary?month=2026-10")).json()
        card = _balance(body)
        assert card["empty"] is True and card["values"]["expense"] == 0.0
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(text("DELETE FROM audit_log WHERE member_id = :m"), {"m": other_id})
            await s.execute(text("DELETE FROM member WHERE id = :m"), {"m": other_id})
            await s.execute(text("DELETE FROM household WHERE id = :h"), {"h": hh_id})
            await s.commit()


@db
async def test_health_tab_is_audited_and_persisted_and_summary_has_no_health_data(
    lab, client
) -> None:
    await lab.add(
        HealthLog(
            member_id=lab.member_id,
            log_type="water",
            value="1.4",
            log_date=datetime.now(UTC).date(),
        )
    )
    token = await _token(lab.member_id)
    summary = (await client.get(f"/api/d/{token}/summary")).text
    assert "water" not in summary and "health" not in summary.lower()
    assert await _count_opened(lab) == 0  # the summary never opens the health tab
    r = await client.get(f"/api/d/{token}/health")
    assert r.status_code == 200 and r.json()["tab"] == "health"
    # read through a brand-new session: proves the audit row was committed, not just flushed
    assert await _count_opened(lab) == 1
    await client.get(f"/api/d/{token}/health")
    assert await _count_opened(lab) == 2


async def _count_opened(lab) -> int:
    async with AsyncSessionLocal() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.member_id == lab.member_id, AuditLog.event == "panel_health_opened")
            )
        ).scalar_one()


@db
async def test_summary_stays_under_300ms_with_20k_rows(lab, client) -> None:
    async with AsyncSessionLocal() as s:
        await s.execute(
            text(
                """
                INSERT INTO expense (id, member_id, household_id, transaction_type, amount,
                                     currency, merchant, category, expense_date, created_at,
                                     status)
                SELECT gen_random_uuid(), :m, :h, 'expense', (g % 90) + 1.25, 'EUR',
                       'm' || (g % 200), 'cat' || (g % 12),
                       timestamptz '2026-10-01 12:00+00' + (g % 28) * interval '1 day',
                       now(), 'paid'
                FROM generate_series(1, 20000) g
                """
            ),
            {"m": lab.member_id, "h": lab.household_id},
        )
        await s.commit()
    token = await _token(lab.member_id)
    await client.get(f"/api/d/{token}/summary?month=2026-10")  # warm the connection pool
    times = []
    for _ in range(3):
        t0 = time.perf_counter()
        r = await client.get(f"/api/d/{token}/summary?month=2026-10")
        times.append(time.perf_counter() - t0)
        assert r.status_code == 200
    assert _balance(r.json())["empty"] is False
    assert statistics.median(times) < 0.3, times
