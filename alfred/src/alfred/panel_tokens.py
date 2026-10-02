"""Dashboard tokens by scope (ADR-0001).

* **Panel token** — read-only, ``dashboard_token_ttl_days`` (7), sliding renewal: asking for the
  dashboard when less than half of the validity is left issues a new token and the previous one
  stops working.
* **Export token** — see ``issue_export_token`` / ``consume_export_token``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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
    """Give ``member`` a usable panel token. Returns True when a new token was written."""
    if member.dashboard_token is not None and not needs_renewal(member, now):
        return False
    rotated = member.dashboard_token is not None
    member.dashboard_token = uuid.uuid4()
    member.dashboard_token_created_at = now or datetime.now(UTC)
    audit(session, "dashboard_link_rotated" if rotated else "dashboard_link_issued", member.id)
    await session.flush()
    return True
