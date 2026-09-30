"""Internal, token-protected product metrics (counts only — no personal data).

``GET /internal/metrics`` with ``Authorization: Bearer <INTERNAL_METRICS_TOKEN>``.
With no token configured the route does not exist (404), so it cannot be probed.
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.db import get_session
from alfred.models import Expense, Member, Message
from alfred.settings import settings

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


def _require_token(authorization: str = Header(default="")) -> None:
    expected = settings.internal_metrics_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=404)
    given = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401)


@router.get("/metrics", dependencies=[Depends(_require_token)])
async def metrics(session: AsyncSession = Depends(get_session)) -> dict:
    now = datetime.now(UTC)
    d1, d7 = now - timedelta(days=1), now - timedelta(days=7)

    async def count(stmt) -> int:
        return int(await session.scalar(stmt) or 0)

    return {
        "generated_at": now.isoformat(),
        "members": {
            state: await count(
                select(func.count()).select_from(Member).where(Member.consent_state == state)
            )
            for state in ("pending", "pending_response", "accepted", "rejected")
        },
        "active_members_7d": await count(
            select(func.count(func.distinct(Message.author_id))).where(
                Message.direction == "inbound", Message.created_at >= d7
            )
        ),
        "inbound_messages_24h": await count(
            select(func.count())
            .select_from(Message)
            .where(Message.direction == "inbound", Message.created_at >= d1)
        ),
        "unprocessed_inbound": await count(
            select(func.count())
            .select_from(Message)
            .where(Message.direction == "inbound", Message.processed.is_(False))
        ),
        "expenses_24h": await count(
            select(func.count()).select_from(Expense).where(Expense.created_at >= d1)
        ),
    }
