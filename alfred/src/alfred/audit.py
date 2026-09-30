"""Append-only audit trail for security/privacy-relevant events (no message content)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from alfred.models import AuditLog


def audit(session: AsyncSession, event: str, member_id: uuid.UUID | None = None, **detail) -> None:
    """Queue one audit row in the caller's transaction (commits/rolls back with it).

    ``detail`` must stay small and free of personal data or message text.
    """
    session.add(AuditLog(id=uuid.uuid4(), member_id=member_id, event=event, detail=detail or None))
