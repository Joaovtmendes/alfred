# ruff: noqa: E501
"""V2-05 — statement import through the router: preview, confirm, repeat, duplicates, undo, limits."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select, update

from alfred import statement
from alfred.db import AsyncSessionLocal
from alfred.models import AuditLog, Expense, ImportBatch, Member
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")

TODAY = date(2026, 10, 14)

ING = (
    '"Datum","Naam / Omschrijving","Rekening","Tegenrekening","Code","Af Bij","Bedrag (EUR)","Mutatiesoort","Mededelingen"\n'
    '"20261012","Albert Heijn 1234 Utrecht","NL01INGB0001234567","","BA","Af","23,45","Betaalautomaat","Pas 012"\n'
    '"20261010","Werkgever BV","NL01INGB0001234567","NL02ABNA0123456789","OV","Bij","2.500,00","Overschrijving","Salaris oktober"\n'
    '"20261009","Onbekende Winkel","NL01INGB0001234567","","BA","Af","9,99","Betaalautomaat","x"\n'
)


@pytest.fixture(autouse=True)
def today(monkeypatch):
    monkeypatch.setattr("alfred.clock.today_local", lambda: TODAY)
    monkeypatch.setattr("alfred.panel_api.today_local", lambda: TODAY)


def _batch_id(lab: Lab) -> str:
    return lab.buttons[-1][0][0].split(":")[1]


async def _expenses(lab: Lab, **where) -> list[Expense]:
    stmt = select(Expense).where(Expense.member_id == lab.member_id).order_by(Expense.expense_date)
    return [r[0] for r in await lab.rows(stmt)]


# ── preview ───────────────────────────────────────────────────────────────────


@db
async def test_preview_counts_and_nothing_is_written_before_the_tap(lab: Lab) -> None:
    reply = await lab.doc("extrato.csv", ING)
    assert "ING" in reply and "3 linhas" in reply and "Novas: 3" in reply
    assert "12/10/2026" in reply and "09/10/2026" in reply
    assert "€2.500,00" in reply  # income total
    assert "Supermercado" in reply or "supermarkt" in reply.lower()
    ids = [b[0].split(":")[0] for b in lab.buttons[-1]]
    assert ids == ["imp_ok", "imp_no"]
    assert len(lab.buttons[-1][0][1]) <= 20 and len(lab.buttons[-1][1][1]) <= 20
    assert await _expenses(lab) == []
    assert "overig" in reply  # the unknown shop is flagged


@db
async def test_confirm_creates_entries_with_source_and_categories(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    reply = await lab.tap(f"imp_ok:{_batch_id(lab)}")
    assert "3 lançamentos importados" in reply
    rows = await _expenses(lab)
    assert [r.source for r in rows] == ["csv"] * 3
    by = {r.merchant: r for r in rows}
    ah = by["Albert Heijn 1234 Utrecht"]
    assert (ah.transaction_type, float(ah.amount), ah.category, ah.status) == (
        "expense",
        23.45,
        "supermarkt",
        "paid",
    )
    pay = by["Werkgever BV"]
    assert (pay.transaction_type, float(pay.amount), pay.category, pay.status) == (
        "income",
        2500.0,
        "inkomen",
        "received",
    )
    assert by["Onbekende Winkel"].category == "overig"
    assert all(r.external_id and r.import_batch_id for r in rows)
    assert all(r.shared is False for r in rows)
    batch = (await lab.rows(select(ImportBatch)))[0][0]
    assert batch.status == "done" and batch.items == [] and batch.rows_new == 3


@db
async def test_sending_the_same_file_again_imports_nothing(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    reply = await lab.doc("extrato.csv", ING)
    assert "nada novo" in reply and lab.buttons[-1] == []
    assert len(await _expenses(lab)) == 3


@db
async def test_a_longer_file_adds_only_the_new_lines(lab: Lab) -> None:
    await lab.doc("a.csv", ING)
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    more = (
        ING
        + '"20261013","Jumbo Utrecht","NL01INGB0001234567","","BA","Af","12,00","Betaalautomaat","x"\n'
    )
    reply = await lab.doc("b.csv", more)
    assert "Novas: 1" in reply and "Já importadas antes: 3" in reply
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    assert len(await _expenses(lab)) == 4


@db
async def test_two_identical_lines_in_one_file_are_both_kept(lab: Lab) -> None:
    csv = "Date,Name,Amount\n2026-10-12,Coffee,-3.00\n2026-10-12,Coffee,-3.00\n"
    reply = await lab.doc("c.csv", csv)
    assert "Novas: 2" in reply
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    assert len(await _expenses(lab)) == 2


# ── possible duplicates of hand-typed entries ─────────────────────────────────


@db
async def test_a_line_matching_a_manual_entry_is_held_back(lab: Lab) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=23.45,
            merchant="Albert Heijn",
            category="supermarkt",
            status="paid",
            expense_date=datetime(2026, 10, 11, 18, tzinfo=UTC),  # a day before the card line
        )
    )
    reply = await lab.doc("extrato.csv", ING)
    assert "Novas: 2" in reply and "Possíveis duplicados de lançamentos seus: 1" in reply
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["imp_ok", "imp_all", "imp_no"]
    bid = _batch_id(lab)
    assert "2 lançamentos importados" in await lab.tap(f"imp_ok:{bid}")
    assert len(await _expenses(lab)) == 3  # the manual one + 2 new

    # sending again and choosing "all" now takes the held-back line too
    await lab.doc("extrato.csv", ING)
    assert "Novas: 0" in lab.sent[-1] and "Já importadas antes: 2" in lab.sent[-1]
    assert [b[0].split(":")[0] for b in lab.buttons[-1]] == ["imp_all", "imp_no"]
    await lab.tap(f"imp_all:{lab.buttons[-1][0][0].split(':')[1]}")
    assert len(await _expenses(lab)) == 4


@db
async def test_a_different_amount_or_name_is_not_a_possible_duplicate(lab: Lab) -> None:
    for amount, merchant in ((24.00, "Albert Heijn"), (23.45, "Kapper")):
        await lab.add(
            Expense(
                member_id=lab.member_id,
                household_id=lab.household_id,
                transaction_type="expense",
                amount=amount,
                merchant=merchant,
                category="overig",
                status="paid",
                expense_date=datetime(2026, 10, 12, 12, tzinfo=UTC),
            )
        )
    reply = await lab.doc("extrato.csv", ING)
    assert "Novas: 3" in reply and "Possíveis duplicados" not in reply


# ── cancel, expire, ownership ─────────────────────────────────────────────────


@db
async def test_cancel_imports_nothing(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    bid = _batch_id(lab)
    reply = await lab.tap(f"imp_no:{bid}")
    assert "Cancelado" in reply and await _expenses(lab) == []
    assert "não está mais aberta" in await lab.tap(f"imp_ok:{bid}")


@db
async def test_a_tap_twice_does_not_import_twice(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    bid = _batch_id(lab)
    await lab.tap(f"imp_ok:{bid}")
    assert "não está mais aberta" in await lab.tap(f"imp_ok:{bid}")
    assert len(await _expenses(lab)) == 3


@db
async def test_somebody_elses_batch_id_does_nothing(lab: Lab, lab2: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    bid = _batch_id(lab)
    assert "não está mais aberta" in await lab2.tap(f"imp_ok:{bid}")
    assert await _expenses(lab2) == [] and await _expenses(lab) == []


@db
async def test_expired_draft_is_refused(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    bid = _batch_id(lab)
    async with AsyncSessionLocal() as s:
        await s.execute(
            update(ImportBatch)
            .where(ImportBatch.id == uuid.UUID(bid))
            .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await s.commit()
    assert "expirou" in await lab.tap(f"imp_ok:{bid}")
    assert await _expenses(lab) == []


@db
async def test_a_new_file_replaces_the_open_draft(lab: Lab) -> None:
    await lab.doc("a.csv", ING)
    first = _batch_id(lab)
    await lab.doc("b.csv", "Date,Name,Amount\n2026-10-12,Lidl,-5.00\n")
    assert "não está mais aberta" in await lab.tap(f"imp_ok:{first}")
    assert (
        await lab.scalar(
            select(func.count()).select_from(ImportBatch).where(ImportBatch.status == "draft")
        )
        >= 1
    )


@db
async def test_garbage_button_id(lab: Lab) -> None:
    assert "não está mais aberta" in await lab.tap("imp_ok:not-a-uuid")


# ── undo ──────────────────────────────────────────────────────────────────────


@db
async def test_undo_removes_only_the_last_import(lab: Lab) -> None:
    await lab.add(
        Expense(
            member_id=lab.member_id,
            household_id=lab.household_id,
            transaction_type="expense",
            amount=4.0,
            merchant="Kiosk",
            category="overig",
            status="paid",
            expense_date=datetime(2026, 10, 1, 12, tzinfo=UTC),
        )
    )
    await lab.doc("extrato.csv", ING)
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    assert len(await _expenses(lab)) == 4
    reply = await lab.say("desfazer importação")
    assert "3 lançamentos" in reply
    left = await _expenses(lab)
    assert [e.merchant for e in left] == ["Kiosk"]
    assert "não há importação" in (await lab.say("desfazer importação")).lower()
    # the undone lines can be imported again
    reply = await lab.doc("extrato.csv", ING)
    assert "Novas: 3" in reply


@db
async def test_help_command(lab: Lab) -> None:
    reply = await lab.say("importar extrato")
    assert "CSV" in reply and "desfazer importação" in reply


# ── rejected files ────────────────────────────────────────────────────────────


@db
async def test_not_a_csv_is_refused_without_downloading(lab: Lab) -> None:
    raw = {
        "type": "document",
        "document": {"id": "m1", "filename": "extrato.pdf", "mime_type": "application/pdf"},
    }
    from tests.conftest import make_message

    dl = AsyncMock(return_value=b"x")
    with patch("alfred.statement.download_media", dl):
        reply = await lab._run(make_message(body=None, raw=raw))
    assert "não é um CSV" in reply
    dl.assert_not_awaited()


@db
async def test_unreadable_files(lab: Lab) -> None:
    assert "Não reconheci" in await lab.doc("x.csv", "hello\nworld\n")
    assert "Não achei" in await lab.doc("x.csv", "")
    assert "Não reconheci" in await lab.doc("x.csv", b"\x00\x01\x02\xff" * 100)
    assert await _expenses(lab) == []


@db
async def test_download_problems(lab: Lab) -> None:
    assert "Não consegui baixar" in await lab.doc("x.csv", None)
    from alfred.whatsapp import MediaTooLarge
    from tests.conftest import make_message

    raw = {
        "type": "document",
        "document": {"id": "m1", "filename": "x.csv", "mime_type": "text/csv"},
    }
    with patch("alfred.statement.download_media", AsyncMock(side_effect=MediaTooLarge)):
        reply = await lab._run(make_message(body=None, raw=raw))
    assert "grande demais" in reply


@db
async def test_hourly_cap(lab: Lab) -> None:
    await lab.add(
        *[
            AuditLog(id=uuid.uuid4(), member_id=lab.member_id, event="statement_upload")
            for _ in range(statement.MAX_PER_HOUR)
        ]
    )
    assert "muitos arquivos" in await lab.doc("x.csv", ING)
    assert await _expenses(lab) == []


@db
async def test_languages_answer_in_their_own_language(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        m = await s.get(Member, lab.member_id)
        m.language = "nl"
        await s.commit()
    reply = await lab.doc("extrato.csv", ING)
    assert "Ik heb je afschrift gelezen" in reply
    assert lab.buttons[-1][0][1].startswith("Importeer")


# ── privacy ───────────────────────────────────────────────────────────────────


@db
async def test_export_and_erase_cover_the_import(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    async with AsyncSessionLocal() as s:
        data = await export_member_data(s, await s.get(Member, lab.member_id))
    assert len(data["tables"]["import_batch"]) == 1
    assert {r["source"] for r in data["tables"]["expense"]} == {"csv"}
    assert "Albert Heijn" in str(data["tables"]["expense"])
    async with AsyncSessionLocal() as s:
        await erase_member(s, await s.get(Member, lab.member_id))
        await s.commit()
    assert (
        await lab.scalar(
            select(func.count())
            .select_from(ImportBatch)
            .where(ImportBatch.member_id == lab.member_id)
        )
        == 0
    )
    assert (
        await lab.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
        )
        == 0
    )


@db
async def test_audit_has_counts_but_no_lines(lab: Lab) -> None:
    await lab.doc("extrato.csv", ING)
    await lab.tap(f"imp_ok:{_batch_id(lab)}")
    events = await lab.rows(
        select(AuditLog.event, AuditLog.detail).where(AuditLog.member_id == lab.member_id)
    )
    names = {e[0] for e in events}
    assert {"statement_upload", "statement_imported"} <= names
    assert "Albert" not in str(events) and "Werkgever" not in str(events)
