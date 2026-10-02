"""Dashboard tokens by scope (ADR-0001).

* **Panel token** — read-only, ``dashboard_token_ttl_days`` (7), sliding renewal: asking for the
  dashboard when less than half of the validity is left issues a new token and the previous one
  stops working.
* **Export token** — see ``issue_export_token`` / ``consume_export_token``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.models import Member
from alfred.settings import settings


def _ttl() -> timedelta:
    return timedelta(days=settings.dashboard_token_ttl_days)


def token_expired(member: Member, now: datetime | None = None) -> bool:
    """A panel link is valid for ``dashboard_token_ttl_days`` from when it was issued."""
    issued = member.dashboard_token_created_at
    if issued is None:  # legacy row: the migration backfills, but never lock the member out
        return False
    return (now or datetime.now(UTC)) - issued > _ttl()


def needs_renewal(member: Member, now: datetime | None = None) -> bool:
    """True when less than half of the validity is left (or the token is already expired)."""
    issued = member.dashboard_token_created_at
    if issued is None:
        return False
    return (now or datetime.now(UTC)) - issued > _ttl() / 2


async def ensure_panel_token(
    session: AsyncSession, member: Member, now: datetime | None = None
) -> bool:
    """Give ``member`` a usable panel token. Returns True when a new token was written.

    The member row is locked first (``FOR UPDATE``) and the two token columns re-read: of two
    simultaneous requests the second waits, sees the first one's token and keeps it, instead of
    rotating again and killing the link the member received first.
    """
    await session.execute(select(Member.id).where(Member.id == member.id).with_for_update())
    await session.refresh(member, ["dashboard_token", "dashboard_token_created_at"])
    if member.dashboard_token is not None and not needs_renewal(member, now):
        return False
    rotated = member.dashboard_token is not None
    member.dashboard_token = uuid.uuid4()
    member.dashboard_token_created_at = now or datetime.now(UTC)
    audit(session, "dashboard_link_rotated" if rotated else "dashboard_link_issued", member.id)
    await session.flush()
    return True


async def issue_export_token(
    session: AsyncSession, member: Member, now: datetime | None = None
) -> uuid.UUID:
    """A fresh single-use export token; any earlier one stops working."""
    now = now or datetime.now(UTC)
    token = uuid.uuid4()
    member.export_token = token
    member.export_token_expires_at = now + timedelta(minutes=settings.export_token_ttl_minutes)
    audit(session, "export_link_issued", member.id)
    await session.flush()
    return token


def _as_uuid(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def peek_export_token(
    session: AsyncSession, token_str: str, now: datetime | None = None
) -> Member | None:
    """The member behind a still-valid export token, without spending it."""
    token = _as_uuid(token_str)
    if token is None:
        return None
    row = await session.execute(
        select(Member).where(
            Member.export_token == token,
            Member.export_token_expires_at > (now or datetime.now(UTC)),
        )
    )
    return row.scalar_one_or_none()


async def consume_export_token(
    session: AsyncSession, token_str: str, now: datetime | None = None
) -> Member | None:
    """Spend the token atomically (UPDATE ... RETURNING): of two racing requests only one wins."""
    token = _as_uuid(token_str)
    if token is None:
        return None
    won = await session.execute(
        update(Member)
        .where(
            Member.export_token == token,
            Member.export_token_expires_at > (now or datetime.now(UTC)),
        )
        .values(export_token=None, export_token_expires_at=None)
        .returning(Member.id)
    )
    member_id = won.scalar_one_or_none()
    if member_id is None:
        return None
    member = await session.get(Member, member_id)
    if member is not None:
        await session.refresh(member)  # the UPDATE bypassed the identity map
    return member
