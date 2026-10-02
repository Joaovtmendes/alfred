"""The one filter function behind every panel card and list (spec §1.2).

Only closed-list values are read from the URL: period, categories, kind, state, trip.
Free text (merchant, person) is filtered in the browser and never reaches the server.
"""

from __future__ import annotations

import re
import uuid
from calendar import monthrange
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Select

from alfred.clock import day_start
from alfred.models import Expense

MAX_RANGE_DAYS = 400
MAX_CATEGORIES = 12
STATES = ("paid", "to_pay", "received", "to_receive")
_KINDS = ("expense", "income")
_CAT_RE = re.compile(r"^[a-z0-9_]{1,50}$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


@dataclass(frozen=True)
class PanelFilter:
    start: date  # inclusive, local day
    end: date  # exclusive, local day
    categories: tuple[str, ...] = ()
    kind: str | None = None
    states: tuple[str, ...] = ()
    trip_id: uuid.UUID | None = None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    return first, first + timedelta(days=monthrange(year, month)[1])


def _parse_day(raw: str | None) -> date | None:
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _parse_period(params: Mapping[str, str], today: date) -> tuple[date, date]:
    default = _month_bounds(today.year, today.month)
    lo, hi = _parse_day(params.get("from")), _parse_day(params.get("to"))
    if lo and hi:
        end = hi + timedelta(days=1)
        if lo < end and (end - lo).days <= MAX_RANGE_DAYS:
            return lo, end
        return default
    m = _MONTH_RE.match(params.get("month") or "")
    if m:
        year, month = int(m.group(1)), int(m.group(2))
        if 2000 <= year <= today.year + 1 and 1 <= month <= 12:
            return _month_bounds(year, month)
    return default


def _closed_list(raw: str | None, allowed: tuple[str, ...]) -> tuple[str, ...]:
    out: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if part in allowed and part not in out:
            out.append(part)
    return tuple(out)


def parse_filter(params: Mapping[str, str], today: date) -> PanelFilter:
    """Read the closed-list URL parameters. Never raises: invalid values fall back to defaults."""
    start, end = _parse_period(params, today)
    cats: list[str] = []
    for part in (params.get("categories") or "").split(","):
        part = part.strip()
        if _CAT_RE.match(part) and part not in cats:
            cats.append(part)
        if len(cats) == MAX_CATEGORIES:
            break
    kind = params.get("kind")
    try:
        trip = uuid.UUID(params["trip"]) if params.get("trip") else None
    except ValueError:
        trip = None
    return PanelFilter(
        start=start,
        end=end,
        categories=tuple(cats),
        kind=kind if kind in _KINDS else None,
        states=_closed_list(params.get("state"), STATES),
        trip_id=trip,
    )


def apply_expense_filter(stmt: Select, f: PanelFilter, member_id: uuid.UUID) -> Select:
    """Add the member scope and every active filter to a statement over ``Expense``."""
    stmt = stmt.where(
        Expense.member_id == member_id,
        Expense.expense_date >= day_start(f.start),
        Expense.expense_date < day_start(f.end),
    )
    if f.categories:
        stmt = stmt.where(Expense.category.in_(f.categories))
    if f.kind:
        stmt = stmt.where(Expense.transaction_type == f.kind)
    if f.states:
        stmt = stmt.where(Expense.status.in_(f.states))
    if f.trip_id:
        stmt = stmt.where(Expense.trip_id == f.trip_id)
    return stmt
