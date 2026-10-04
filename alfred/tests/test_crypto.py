"""S1-06 — application-level encryption of art. 9 health data (Fernet, key from the environment)."""

from __future__ import annotations

import os
from datetime import date

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select, text

from alfred import crypto
from alfred.db import AsyncSessionLocal
from alfred.models import HealthLog
from alfred.settings import settings
from tests.labkit import Lab

db = pytest.mark.skipif(os.environ.get("ALFRED_TEST_DB") != "1", reason="needs test DB")


@pytest.fixture
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(k))
    return k


def test_round_trip_and_ciphertext_is_not_the_plaintext(key) -> None:
    token = crypto.encrypt("omeprazol 20 mg")
    assert token.startswith(crypto.PREFIX) and "omeprazol" not in token
    assert crypto.decrypt(token) == "omeprazol 20 mg"


def test_without_a_key_text_is_stored_as_is_and_old_rows_stay_readable(monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(""))
    assert crypto.encrypt("7") == "7"
    assert crypto.decrypt("7") == "7"  # a row written before encryption existed


def test_an_encrypted_value_without_the_key_fails_loudly_not_silently(key, monkeypatch) -> None:
    token = crypto.encrypt("secret")
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(""))
    with pytest.raises(crypto.KeyMissingError):
        crypto.decrypt(token)


def test_key_rotation_new_key_first_old_key_still_reads(monkeypatch) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(old))
    token = crypto.encrypt("x")
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(f"{new},{old}"))
    assert crypto.decrypt(token) == "x"
    assert crypto.decrypt(crypto.encrypt("y")) == "y"
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(new))
    assert crypto.decrypt(crypto.encrypt("z")) == "z"
    with pytest.raises(crypto.KeyMissingError):
        crypto.decrypt(token)  # the old key was dropped


def test_none_and_empty_pass_through(key) -> None:
    assert crypto.encrypt(None) is None and crypto.decrypt(None) is None
    assert crypto.decrypt("") == ""


@db
@pytest.mark.asyncio
async def test_health_values_are_ciphertext_in_the_table_and_plain_through_the_orm(
    lab: Lab, key
) -> None:
    await lab.add(
        HealthLog(
            member_id=lab.member_id,
            log_type="medication",
            value="omeprazol",
            notes="depois do café",
            log_date=date(2026, 10, 1),
        )
    )
    async with AsyncSessionLocal() as s:
        raw = (
            await s.execute(
                text("select value, notes from health_log where member_id = :m"),
                {"m": lab.member_id},
            )
        ).one()
        assert raw.value.startswith(crypto.PREFIX) and raw.notes.startswith(crypto.PREFIX)
        assert "omeprazol" not in raw.value and "café" not in raw.notes
        got = (
            await s.execute(
                select(HealthLog.value, HealthLog.notes).where(HealthLog.member_id == lab.member_id)
            )
        ).one()
        assert tuple(got) == ("omeprazol", "depois do café")


@db
@pytest.mark.asyncio
async def test_backfill_encrypts_old_plaintext_rows_once(lab: Lab, key, monkeypatch) -> None:
    from alfred.crypto import backfill_health

    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(""))
    await lab.add(
        HealthLog(member_id=lab.member_id, log_type="mood", value="7", log_date=date(2026, 10, 1))
    )
    monkeypatch.setattr(settings, "data_encryption_key", SecretStr(key))
    async with AsyncSessionLocal() as s:
        assert await backfill_health(s) >= 1
        assert await backfill_health(s) == 0  # idempotent
        await s.commit()
        raw = await s.scalar(
            text("select value from health_log where member_id = :m"), {"m": lab.member_id}
        )
        assert raw.startswith(crypto.PREFIX)
