"""M11 — Personal dashboard (token-based, no login required).

Routes:
  GET  /d/{token}                → the v2 panel (the only one since 04/10/2026; the v1 page and
                                   its /api/d/{token} data endpoint were removed)
  GET  /api/d/{token}/{tab}      → per-tab JSON (see ``panel_api``)
  GET  /api/d/{token}/export     → confirmation page; POST → GDPR data export (single-use link)
"""

from __future__ import annotations

import uuid
from html import escape

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred import panel
from alfred.audit import audit
from alfred.dashboard_i18n import normalize_lang, ui
from alfred.db import get_session
from alfred.models import Member
from alfred.panel_tokens import consume_export_token, peek_export_token, token_expired
from alfred.panel_tokens import ensure_panel_token as ensure_dashboard_token  # noqa: F401
from alfred.web_security import limit_dashboard

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["dashboard"])


# ── helpers ───────────────────────────────────────────────────────────────────


async def _find_member(token_str: str, session: AsyncSession) -> Member | None:
    """The member holding this token, expired or not (None for unknown or malformed tokens)."""
    try:
        token = uuid.UUID(token_str)
    except ValueError:
        return None
    result = await session.execute(select(Member).where(Member.dashboard_token == token))
    return result.scalar_one_or_none()


async def _get_member(token_str: str, session: AsyncSession) -> Member:
    member = await _find_member(token_str, session)
    if member is None or token_expired(member):
        raise HTTPException(status_code=404, detail="Dashboard not found") from None
    return member


def _link_gone(lang: str | None) -> HTMLResponse:
    """What a person sees when tapping an old or wrong link: how to get a new one, not JSON."""
    lang = normalize_lang(lang)
    t = ui(lang)
    body = (
        f'<!doctype html><html lang="{lang}"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light dark">'
        f"<title>{escape(t['link_expired_title'])}</title>"
        "<body><main>"
        f"<h1>{escape(t['link_expired_title'])}</h1><p>{escape(t['link_expired_text'])}</p>"
        "</main></body></html>"
    )
    return HTMLResponse(body, status_code=404)


@router.get(
    "/api/d/{token}/export", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def dashboard_export_page(
    token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Confirmation page. Link previews and bots only GET, so they cannot spend the link."""
    member = await peek_export_token(session, token)
    if member is None:
        raise HTTPException(status_code=404, detail="Link not found")
    lang = normalize_lang(member.language)
    t = ui(lang)
    body = (
        f'<!doctype html><html lang="{lang}"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{t['export_title']}</title>"
        "<body>"
        f"<h1>{t['export_title']}</h1><p>{t['export_text']}</p>"
        '<form method="post"><button type="submit">'
        f"{t['export_button']}</button></form></body></html>"
    )
    return HTMLResponse(body)


@router.post(
    "/api/d/{token}/export", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def dashboard_export(
    token: str, session: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """GDPR access/portability: everything stored about the member, once, as a JSON download."""
    from alfred.privacy import export_member_data

    member = await consume_export_token(session, token)
    if member is None:
        raise HTTPException(status_code=404, detail="Link not found")
    audit(session, "data_exported", member.id)
    return JSONResponse(
        await export_member_data(session, member),
        headers={"Content-Disposition": 'attachment; filename="alfred-data.json"'},
    )


def _books_year(raw: str | None) -> int:
    from alfred.clock import today_local

    today = today_local().year
    if raw is None or not raw.isascii() or not raw.isdigit() or len(raw) != 4:
        return today
    return min(max(int(raw), 2000), today)


@router.get(
    "/api/d/{token}/books-export", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def books_export_page(
    token: str, year: str | None = None, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """V2-12 — confirmation page of the accountant CSV (a GET never spends the single-use link)."""
    member = await peek_export_token(session, token)
    if member is None:
        raise HTTPException(status_code=404, detail="Link not found")
    lang = normalize_lang(member.language)
    t = ui(lang)
    body = (
        f'<!doctype html><html lang="{lang}"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{t['books_export_title']}</title>"
        "<body>"
        f"<h1>{t['books_export_title']}</h1><p>{t['books_export_text']}</p>"
        f'<form method="post" action="?year={_books_year(year)}"><button type="submit">'
        f"{t['books_export_button']}</button></form></body></html>"
    )
    return HTMLResponse(body)


@router.post(
    "/api/d/{token}/books-export", include_in_schema=False, dependencies=[Depends(limit_dashboard)]
)
async def books_export(
    token: str, year: str | None = None, session: AsyncSession = Depends(get_session)
) -> Response:
    """The year's entries as CSV (business entries only in business mode), once."""
    from alfred import accounting

    member = await consume_export_token(session, token)
    if member is None:
        raise HTTPException(status_code=404, detail="Link not found")
    y = _books_year(year)
    audit(session, "accounting_exported", member.id, year=y)
    body = await accounting.export_csv(session, member, y)
    return Response(
        "\ufeff" + body,  # the byte-order mark makes Excel read the accents right
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="alfred-accounting-{y}.csv"'},
    )


# ── HTML page ─────────────────────────────────────────────────────────────────


@router.get("/panel-assets/{name}", include_in_schema=False)
async def panel_asset(name: str) -> Response:
    """CSS, JS and fonts of the v2 panel: public (no secret in them), fixed whitelist."""
    found = panel.asset(name)
    if found is None:
        raise HTTPException(status_code=404, detail="Not found")
    body, ctype = found
    return Response(
        body, media_type=ctype, headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


@router.get("/d/{token}", include_in_schema=False, dependencies=[Depends(limit_dashboard)])
async def dashboard_page(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    member = await _find_member(token, session)
    if member is None or token_expired(member):
        return _link_gone(member.language if member else None)
    lang = normalize_lang(member.language)
    return HTMLResponse(
        panel.render_v2(
            request.state.csp_nonce,
            lang,
            token,
            member.display_name,
        )
    )
