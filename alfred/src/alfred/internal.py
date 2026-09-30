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
from alfred.models import Expense, LlmUsage, Member, Message
from alfred.settings import settings


def retention(first_last: list[tuple[datetime, datetime]], now: datetime, days: int) -> dict:
    """Day-N retention: of members whose first message is at least N days old, the share who
    wrote again on or after day N. ``first_last`` = (first, last) inbound message per member."""
    eligible = [(first, last) for first, last in first_last if first <= now - timedelta(days=days)]
    kept = sum(1 for first, last in eligible if last >= first + timedelta(days=days))
    return {
        "eligible": len(eligible),
        "retained": kept,
        "rate": round(kept / len(eligible), 3) if eligible else None,
    }


def llm_cost_eur(input_tokens: int, output_tokens: int) -> float:
    """Estimated cost (EUR) from token counts and the configured list prices."""
    usd = (
        input_tokens * settings.llm_input_usd_per_mtok
        + output_tokens * settings.llm_output_usd_per_mtok
    ) / 1_000_000
    return round(usd * settings.usd_to_eur, 4)


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

    d30 = now - timedelta(days=30)
    span = (
        await session.execute(
            select(
                func.min(Message.created_at).label("first"),
                func.max(Message.created_at).label("last"),
            )
            .where(Message.direction == "inbound", Message.author_id.is_not(None))
            .group_by(Message.author_id)
        )
    ).all()
    first_last = [(r.first, r.last) for r in span]

    usage = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                func.count(func.distinct(LlmUsage.member_id)),
            ).where(LlmUsage.created_at >= d30)
        )
    ).one()
    calls_30d, tok_in, tok_out, members_30d = (int(x) for x in usage)
    cost_30d = llm_cost_eur(tok_in, tok_out)

    return {
        "generated_at": now.isoformat(),
        "retention": {
            "d7": retention(first_last, now, 7),
            "d30": retention(first_last, now, 30),
        },
        "llm_30d": {
            "calls": calls_30d,
            "input_tokens": tok_in,
            "output_tokens": tok_out,
            "estimated_cost_eur": cost_30d,
            "members_with_calls": members_30d,
            "estimated_cost_eur_per_member": round(cost_30d / members_30d, 4)
            if members_30d
            else None,
        },
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
