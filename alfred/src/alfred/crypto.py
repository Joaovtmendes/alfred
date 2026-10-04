"""S1-06 — application-level encryption of art. 9 health data.

``HealthLog.value`` and ``.notes`` (medication names, readings, free notes) are stored as
Fernet tokens prefixed ``enc1:``. The key comes from ``DATA_ENCRYPTION_KEY`` (a Fernet key, or
several separated by commas: the first encrypts, all of them decrypt, which is how a key is
rotated). Behaviour is chosen so that turning the feature on never breaks a running system:

* no key set → values are written as plain text and a warning is logged at startup;
* a value without the prefix is read as plain text (rows written before encryption);
* a prefixed value without a usable key raises :class:`KeyMissingError`; it never returns
  ciphertext as if it were data, and never silently drops it.

Losing the key means losing the health data it protects: keep it in the Railway variables *and*
in a password manager. ``python -m alfred.crypto backfill`` encrypts the rows written earlier.
"""

from __future__ import annotations

import asyncio
import sys

import structlog
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Text, TypeDecorator

from alfred.settings import settings

logger = structlog.get_logger()

PREFIX = "enc1:"


class KeyMissingError(RuntimeError):
    """An encrypted value cannot be read: no key, or none of the keys fits."""


def _fernet() -> MultiFernet | None:
    raw = settings.data_encryption_key.get_secret_value().strip()
    if not raw:
        return None
    return MultiFernet([Fernet(k.strip().encode()) for k in raw.split(",") if k.strip()])


def enabled() -> bool:
    return _fernet() is not None


def encrypt(value: str | None) -> str | None:
    if value is None or value == "":
        return value
    f = _fernet()
    if f is None or value.startswith(PREFIX):
        return value
    return PREFIX + f.encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None or not value.startswith(PREFIX):
        return value
    f = _fernet()
    if f is None:
        raise KeyMissingError("DATA_ENCRYPTION_KEY is not set but encrypted data exists")
    try:
        return f.decrypt(value[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise KeyMissingError("none of the DATA_ENCRYPTION_KEY keys opens this value") from exc


class EncryptedText(TypeDecorator):
    """A ``Text`` column whose content is encrypted when a key is configured."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ARG002
        return encrypt(value)

    def process_result_value(self, value, dialect):  # noqa: ARG002
        return decrypt(value)


def warn_if_unprotected(environment: str) -> None:
    """Called at startup: production without a key stores health data unencrypted."""
    if environment == "production" and not enabled():
        logger.warning("crypto.no_key", detail="health data is stored without app-level encryption")


async def backfill_health(session: AsyncSession) -> int:
    """Encrypt health rows still stored as plain text; returns how many rows changed."""
    from alfred.models import HealthLog

    if not enabled():
        raise KeyMissingError("set DATA_ENCRYPTION_KEY before running the backfill")
    # read raw (the column type would decrypt/ignore): select the table column's impl
    value_col = HealthLog.__table__.c.value.cast(Text)
    notes_col = HealthLog.__table__.c.notes.cast(Text)
    rows = (await session.execute(select(HealthLog.__table__.c.id, value_col, notes_col))).all()
    changed = 0
    for row_id, value, notes in rows:
        needs = (value and not value.startswith(PREFIX)) or (notes and not notes.startswith(PREFIX))
        if not needs:
            continue
        await session.execute(
            update(HealthLog.__table__)
            .where(HealthLog.__table__.c.id == row_id)
            .values(value=decrypt(value), notes=decrypt(notes))
        )
        changed += 1
    await session.flush()
    return changed


async def _main() -> None:
    from alfred.db import AsyncSessionLocal

    async with AsyncSessionLocal() as s:
        n = await backfill_health(s)
        await s.commit()
    print(f"{n} health rows encrypted")  # noqa: T201


if __name__ == "__main__":
    if sys.argv[1:] != ["backfill"]:
        sys.exit("usage: python -m alfred.crypto backfill")
    asyncio.run(_main())
