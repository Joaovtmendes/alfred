"""Per-tab JSON for the v2 panel. Each tab loads only what it shows (spec §2).

Every route: token check (404 when unknown, malformed or expired), IP rate limit, the single
closed-list filter, and ``Cache-Control: no-store`` (added by the security middleware).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.clock import today_local
from alfred.dashboard import _get_member
from alfred.dashboard_i18n import normalize_lang
from alfred.db import get_session
from alfred.models import SETTLED, Expense, Member
from alfred.panel_filters import PanelFilter, apply_expense_filter, parse_filter
from alfred.web_security import limit_dashboard

router = APIRouter(tags=["panel"])


def _filter_json(f: PanelFilter) -> dict[str, Any]:
    return {
        "start": f.start.isoformat(),
        "end": f.end.isoformat(),
        "categories": list(f.categories),
        "kind": f.kind,
        "states": list(f.states),
        "trip": str(f.trip_id) if f.trip_id else None,
    }


def _envelope(
    tab: str, member: Member, f: PanelFilter, cards: list[dict[str, Any]]
) -> JSONResponse:
    return JSONResponse(
        {
            "tab": tab,
            "lang": normalize_lang(member.language),
            "filter": _filter_json(f),
            "cards": cards,
        }
    )


async def _balance_card(session: AsyncSession, member: Member, f: PanelFilter) -> dict[str, Any]:
    """Income, expense and balance of the settled entries: one aggregate query in the database."""
    stmt = apply_expense_filter(
        select(
            func.coalesce(
                func.sum(case((Expense.transaction_type == "income", Expense.amount), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((Expense.transaction_type == "expense", Expense.amount), else_=0)), 0
            ),
            func.count(),
        ).where(Expense.status.in_(SETTLED)),
        f,
        member.id,
    )
    income, expense, n = (await session.execute(stmt)).one()
    income, expense = round(float(income), 2), round(float(expense), 2)
    return {
        "id": "balance",
        "empty": n == 0,
        "values": {"income": income, "expense": expense, "balance": round(income - expense, 2)},
        "phrase": None,  # filled by the "Resumo e Dinheiro" PR once the projection exists
    }


async def _open(token: str, request: Request, session: AsyncSession) -> tuple[Member, PanelFilter]:
    member = await _get_member(token, session)
    return member, parse_filter(dict(request.query_params), today_local())


@router.get(
    "/api/d/{token}/summary", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def summary(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    return _envelope("summary", member, f, [await _balance_card(session, member, f)])


@router.get(
    "/api/d/{token}/money", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def money(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    return _envelope("money", member, f, [])


@router.get(
    "/api/d/{token}/health", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def health(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """Own endpoint, loaded only when the tab opens; every opening is audited (no content)."""
    member, f = await _open(token, request, session)
    audit(session, "panel_health_opened", member.id)
    # get_session also commits on exit, but the audit row must not depend on that ordering.
    await session.commit()
    return _envelope("health", member, f, [])


@router.get(
    "/api/d/{token}/agenda", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def agenda(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    return _envelope("agenda", member, f, [])


@router.get(
    "/api/d/{token}/trips", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def trips(
    token: str, request: Request, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    member, f = await _open(token, request, session)
    return _envelope("trips", member, f, [])
