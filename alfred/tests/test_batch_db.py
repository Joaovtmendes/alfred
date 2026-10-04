"""V2-17 — a message with 2+ entries is confirmed before anything is written."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from alfred.batch import clean_items
from alfred.clock import now_local
from alfred.conversation import _claims_recorded, _t
from alfred.db import AsyncSessionLocal
from alfred.models import Expense, PendingBatch
from alfred.privacy import erase_member, export_member_data
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


def _it(amount, merchant, category="restaurant", currency="EUR", kind="expense"):
    return {
        "amount": amount,
        "merchant": merchant,
        "category": category,
        "currency": currency,
        "type": kind,
        "description": None,
        "days_ago": 0,
    }


async def _count(lab: Lab) -> int:
    return await lab.scalar(
        select(func.count()).select_from(Expense).where(Expense.member_id == lab.member_id)
    )


async def _batches(lab: Lab) -> list[PendingBatch]:
    async with AsyncSessionLocal() as s:
        return list(
            (await s.execute(select(PendingBatch).where(PendingBatch.member_id == lab.member_id)))
            .scalars()
            .all()
        )


async def _draft(lab: Lab, *items) -> str:
    lab.multi.return_value = list(items or [_it(3.5, "Café"), _it(8, "Padaria")])
    return await lab.say("café 3,50 e padaria 8")


# ── pure ──────────────────────────────────────────────────────────────────────


def test_clean_items_drops_zero_negative_huge_and_foreign() -> None:
    items = [
        _it(5, "a"),
        _it(0, "b"),
        _it(-3, "c"),
        _it(2_000_000, "d"),
        _it(4, "e", currency="USD"),
        _it(float("nan"), "f"),
        _it(7, "g"),
    ]
    assert [i["merchant"] for i in clean_items(items)] == ["a", "g"]


@pytest.mark.parametrize("lang", ["pt", "nl", "en", "fr", "de"])
def test_the_draft_never_claims_to_have_recorded(lang: str) -> None:
    for key in ("batch_title", "batch_ask", "batch_edit_hint", "batch_cancelled", "batch_empty"):
        assert not _claims_recorded(_t(key, lang, n=2)), (key, lang)
    assert _claims_recorded(_t("multi_recorded_title", lang, n=2))  # after confirming it may


# ── draft / confirm / cancel ──────────────────────────────────────────────────


@db
async def test_two_items_are_a_draft_with_three_buttons(lab: Lab) -> None:
    reply = await _draft(lab)
    assert "Café" in reply and "Padaria" in reply and "1." in reply and "2." in reply
    assert not _claims_recorded(reply)
    assert await _count(lab) == 0
    (batch,) = await _batches(lab)
    ids = [b[0] for b in lab.buttons[-1]]
    assert ids == [f"batch_ok:{batch.id}", f"batch_edit:{batch.id}", f"batch_cancel:{batch.id}"]
    assert batch.expires_at > now_local() + timedelta(minutes=14)


@db
async def test_confirm_by_button_writes_once_and_a_second_tap_does_nothing(lab: Lab) -> None:
    await _draft(lab)
    (batch,) = await _batches(lab)
    reply = await lab.tap(f"batch_ok:{batch.id}")
    assert "(2)" in reply and await _count(lab) == 2
    again = await lab.tap(f"batch_ok:{batch.id}")
    assert await _count(lab) == 2  # idempotent
    assert again == _t("batch_gone", "pt") or "pendente" in again
    assert await _batches(lab) == []


@db
async def test_confirm_by_text_and_in_other_words(lab: Lab) -> None:
    await _draft(lab)
    assert "(2)" in await lab.say("sim")
    assert await _count(lab) == 2
    await _draft(lab)
    assert "(2)" in await lab.say("pode confirmar")
    assert await _count(lab) == 4


@db
async def test_cancel_by_button_and_by_text(lab: Lab) -> None:
    await _draft(lab)
    (batch,) = await _batches(lab)
    assert "Cancelado" in await lab.tap(f"batch_cancel:{batch.id}")
    await _draft(lab)
    assert "Cancelado" in await lab.say("não")
    assert await _count(lab) == 0 and await _batches(lab) == []


@db
async def test_a_bare_yes_without_a_draft_is_still_the_old_answer(lab: Lab) -> None:
    reply = await lab.say("sim")
    assert reply == _t("bare_yes", "pt") or "nada" in reply.lower() or reply
    assert await _count(lab) == 0


@db
async def test_a_single_expense_still_goes_straight_in(lab: Lab) -> None:
    lab.expense.return_value = _it(12, "Jumbo", "boodschappen")
    await lab.say("jumbo 12")
    assert await _count(lab) == 1 and await _batches(lab) == []


# ── adjust ────────────────────────────────────────────────────────────────────


@db
async def test_adjust_remove_the_second(lab: Lab) -> None:
    await _draft(lab, _it(3.5, "Café"), _it(8, "Padaria"), _it(20, "Mercado"))
    reply = await lab.say("tira o segundo")
    assert "Padaria" not in reply and "Café" in reply and "Mercado" in reply
    assert "1." in reply and "2." in reply and "3." not in reply
    assert "(2)" in await lab.say("ok")
    assert await _count(lab) == 2


@db
async def test_adjust_change_the_amount_of_the_first(lab: Lab) -> None:
    await _draft(lab)
    reply = await lab.say("o primeiro foi 4")
    assert "4,00" in reply and "3,50" not in reply
    await lab.say("sim")
    rows = ((await _amounts(lab)),)[0]
    assert sorted(rows) == [4.0, 8.0]


async def _amounts(lab: Lab) -> list[float]:
    async with AsyncSessionLocal() as s:
        return [
            float(a)
            for a in (
                await s.execute(select(Expense.amount).where(Expense.member_id == lab.member_id))
            )
            .scalars()
            .all()
        ]


@db
async def test_adjust_invalid_amount_and_unknown_item_keep_the_draft(lab: Lab) -> None:
    await _draft(lab)
    assert "não serve" in await lab.say("o primeiro foi 0")
    assert "Não achei" in await lab.say("o quinto foi 5")
    (batch,) = await _batches(lab)
    assert [i["amount"] for i in batch.items] == [3.5, 8.0]


@db
async def test_removing_everything_cancels_the_draft(lab: Lab) -> None:
    await _draft(lab)
    await lab.say("tira o primeiro")
    reply = await lab.say("tira o primeiro")
    assert "cancelei" in reply and await _batches(lab) == []


@db
async def test_adjust_keywords_in_other_languages(lab: Lab) -> None:
    await _draft(lab, _it(3.5, "Koffie"), _it(8, "Bakker"), _it(2, "Krant"))
    assert "Bakker" not in await lab.say("remove the second")
    assert "Krant" not in await lab.say("haal de laatste weg")
    assert "Koffie" in await lab.say("the first was 5")


# ── expiry, two in a row, invalid items ───────────────────────────────────────


@db
async def test_expired_draft_is_discarded_with_a_notice(lab: Lab) -> None:
    await _draft(lab)
    async with AsyncSessionLocal() as s:
        for b in (await s.execute(select(PendingBatch))).scalars():
            b.expires_at = now_local() - timedelta(minutes=1)
        await s.commit()
    reply = await lab.say("sim")
    assert "expirou" in reply and await _count(lab) == 0 and await _batches(lab) == []


@db
async def test_expired_draft_button_tap(lab: Lab) -> None:
    await _draft(lab)
    (batch,) = await _batches(lab)
    async with AsyncSessionLocal() as s:
        row = await s.get(PendingBatch, batch.id)
        row.expires_at = now_local() - timedelta(seconds=1)
        await s.commit()
    assert "expirou" in await lab.tap(f"batch_ok:{batch.id}")
    assert await _count(lab) == 0


@db
async def test_two_drafts_in_a_row_the_newer_replaces_the_older(lab: Lab) -> None:
    await _draft(lab, _it(1, "Um"), _it(2, "Dois"))
    (first,) = await _batches(lab)
    await _draft(lab, _it(10, "Três"), _it(20, "Quatro"))
    (second,) = await _batches(lab)
    assert first.id != second.id
    assert "pendente" in await lab.tap(f"batch_ok:{first.id}")  # old buttons are dead
    assert await _count(lab) == 0
    await lab.tap(f"batch_ok:{second.id}")
    assert sorted(await _amounts(lab)) == [10.0, 20.0]


@db
async def test_invalid_item_is_dropped_and_one_left_is_recorded_directly(lab: Lab) -> None:
    await _draft(lab, _it(0, "Nada"), _it(9, "Padaria"))
    assert await _count(lab) == 1 and await _batches(lab) == []


@db
async def test_invalid_item_dropped_from_a_bigger_draft(lab: Lab) -> None:
    reply = await _draft(lab, _it(0, "Nada"), _it(9, "Padaria"), _it(3, "Café"))
    assert "Nada" not in reply and "Padaria" in reply and "Café" in reply
    (batch,) = await _batches(lab)
    assert len(batch.items) == 2


@db
async def test_forged_button_id_of_another_member_does_nothing(lab: Lab) -> None:
    await _draft(lab)
    assert "pendente" in await lab.tap(f"batch_ok:{uuid.uuid4()}")
    assert "pendente" in await lab.tap("batch_ok:not-a-uuid")
    assert await _count(lab) == 0 and len(await _batches(lab)) == 1


# ── privacy ───────────────────────────────────────────────────────────────────


@db
async def test_pending_batch_is_exported_and_erased(lab: Lab) -> None:
    await _draft(lab)
    from alfred.models import Member

    async with AsyncSessionLocal() as s:
        member = await s.get(Member, lab.member_id)
        data = await export_member_data(s, member)
        assert "pending_batch" in str(data)
        await erase_member(s, member)
        await s.commit()
    async with AsyncSessionLocal() as s:
        left = await s.scalar(
            select(func.count())
            .select_from(PendingBatch)
            .where(PendingBatch.member_id == lab.member_id)
        )
        assert left == 0


# ── undo the whole batch (backlog) ────────────────────────────────────────────


@db
async def test_a_confirmed_batch_offers_to_undo_all_of_it_and_undo_removes_only_that_batch(
    lab: Lab,
) -> None:
    other = _it(50, "Outro", "overig")
    lab.expense.return_value = {**other, "is_expense": True}
    await lab.say("outro 50")  # an unrelated, earlier entry
    before = await _count(lab)
    await _draft(lab)
    (batch,) = await _batches(lab)
    await lab.tap(f"batch_ok:{batch.id}")
    assert await _count(lab) == before + 2
    undo = [b for b in lab.buttons[-1] if b[0].startswith("batch_undo:")]
    assert len(undo) == 1
    reply = await lab.tap(undo[0][0])
    assert await _count(lab) == before and "2" in reply
    again = await lab.tap(undo[0][0])  # a second tap does nothing
    assert await _count(lab) == before and again != reply


@db
async def test_undo_all_never_touches_another_members_entries(lab: Lab) -> None:
    import uuid as _uuid

    reply = await lab.tap(f"batch_undo:{_uuid.uuid4()},{_uuid.uuid4()}")
    assert await _count(lab) == 0 and reply
    reply = await lab.tap("batch_undo:not-a-uuid")
    assert reply
