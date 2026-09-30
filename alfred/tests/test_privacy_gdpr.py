"""Sprint 5 — GDPR erase/export, metrics endpoint, Sentry scrubbing."""

from __future__ import annotations

import time
import uuid

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select

from alfred import observability
from alfred.conversation import _EXPORT_RE, _WIPE_RE
from alfred.db import Base, engine
from alfred.models import AuditLog, Expense, Household, Member, Message
from alfred.settings import settings


@pytest.mark.parametrize(
    "text",
    [
        "apagar meus dados",
        "apaga os meus dados",
        "delete my data",
        "verwijder mijn gegevens",
        "supprimer mes donnees",
        "meine daten loschen",
    ],
)
def test_wipe_phrases(text) -> None:
    assert _WIPE_RE.match(text)


@pytest.mark.parametrize("text", ["apaga", "apaga o treino", "delete", "apaga tarefa meus dados x"])
def test_wipe_does_not_swallow_other_commands(text) -> None:
    assert not _WIPE_RE.match(text)


def test_export_phrases() -> None:
    assert _EXPORT_RE.match("exportar meus dados") and _EXPORT_RE.match("export my data")


async def _add_expense(lab) -> None:
    await lab.add(
        Expense(
            id=uuid.uuid4(),
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=10,
            currency="EUR",
            category="overig",
            expense_date=__import__("alfred.clock", fromlist=["now_local"]).now_local(),
        )
    )


async def test_wipe_asks_first_and_deletes_nothing(lab) -> None:
    await _add_expense(lab)
    reply = await lab.say("apagar meus dados")
    assert "tudo" in reply and "Quer mesmo" in reply
    ids = [b[0] for b in lab.buttons[-1]]
    assert ids[0].startswith("wipe:") and ids[1] == "keep:0"
    assert await lab.scalar(select(func.count()).select_from(Expense)) >= 1


async def test_keep_button_cancels(lab) -> None:
    await _add_expense(lab)
    assert "não apaguei nada" in await lab.tap("keep:0")
    assert await lab.scalar(select(Member).where(Member.id == lab.member_id)) is not None


async def test_stale_or_forged_wipe_button_is_refused(lab) -> None:
    old = int(time.time()) - 3600
    assert "expirou" in await lab.tap(f"wipe:{old}")
    assert "expirou" in await lab.tap("wipe:abc")
    assert await lab.scalar(select(Member).where(Member.id == lab.member_id)) is not None


async def test_confirmed_wipe_removes_every_trace(lab) -> None:
    await _add_expense(lab)
    await lab.say("meu dashboard")  # creates an audit row
    reply = await lab.tap(f"wipe:{int(time.time())}")
    assert "apaguei tudo" in reply

    assert await lab.scalar(select(Member).where(Member.id == lab.member_id)) is None
    assert await lab.scalar(select(Household).where(Household.id == lab.household_id)) is None
    # every table that points at a person is empty for this member
    for table in Base.metadata.sorted_tables:
        for col in ("member_id", "author_id"):
            if col in table.c:
                n = await lab.scalar(
                    select(func.count()).select_from(table).where(table.c[col] == lab.member_id)
                )
                assert n == 0, f"{table.name}.{col} still has rows"
    # audit trail survives, anonymised
    assert (
        await lab.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.event == "member_erased")
        )
        >= 1
    )
    assert (
        await lab.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.household_id == lab.household_id)
        )
        == 0
    )


async def test_export_link_needs_base_url_and_returns_json(lab, client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "base_url", "https://alfred.example")
    await _add_expense(lab)
    reply = await lab.say("exportar meus dados")
    assert "https://alfred.example/api/d/" in reply and reply.rstrip().endswith("/export")
    token = reply.rsplit("/api/d/", 1)[1].split("/export")[0]

    await engine.dispose()
    r = await client.get(f"/api/d/{token}/export")
    assert r.status_code == 200
    assert "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["member_id"] == str(lab.member_id)
    assert len(body["tables"]["expense"]) == 1
    assert "dashboard_token" not in body["tables"]["member"][0]
    assert r.headers["cache-control"] == "no-store"


async def test_metrics_endpoint_is_hidden_without_token_and_counts_with_it(
    client, monkeypatch
) -> None:
    await engine.dispose()
    monkeypatch.setattr(settings, "internal_metrics_token", SecretStr(""))
    assert (await client.get("/internal/metrics")).status_code == 404
    monkeypatch.setattr(settings, "internal_metrics_token", SecretStr("s3cret"))
    assert (await client.get("/internal/metrics")).status_code == 401
    assert (
        await client.get("/internal/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    r = await client.get("/internal/metrics", headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 200
    assert set(r.json()) >= {"members", "active_members_7d", "unprocessed_inbound"}


def test_sentry_scrub_removes_personal_data() -> None:
    event = {
        "request": {"data": "Jumbo 20", "headers": {"x": "y"}, "cookies": {}, "url": "/webhook"},
        "user": {"id": "1"},
        "breadcrumbs": {"values": [{"message": "m", "data": {"body": "secret"}}]},
    }
    out = observability._scrub(event, {})
    assert "data" not in out["request"] and "headers" not in out["request"]
    assert out["request"]["url"] == "/webhook"
    assert "user" not in out
    assert "data" not in out["breadcrumbs"]["values"][0]


def test_alert_is_a_noop_for_sentry_when_disabled() -> None:
    observability.alert("test.alert", member_id="x")  # must not raise
