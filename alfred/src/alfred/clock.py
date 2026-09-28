"""Single source of "now" and "today" for user-facing dates.

Alfred's users live in the Netherlands: "today", "this week" and "this month"
mean Europe/Amsterdam days, never UTC days. Between 00:00 and 01:00/02:00
local time UTC is still yesterday, which used to file expenses, water and
habit check-ins on the wrong day. Every "what day is it for the user" question
goes through this module (the timezone comes from ``settings.timezone``).

Storage stays UTC (``timestamptz``); only day boundaries are local.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from alfred.settings import settings


def local_tz() -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def now_local() -> datetime:
    """Current moment as an aware datetime in the user's timezone."""
    return datetime.now(UTC).astimezone(local_tz())


def today_local() -> date:
    return now_local().date()


def to_local(dt: datetime) -> datetime:
    """Convert a stored (UTC/aware) datetime to the user's timezone."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(local_tz())


def day_start(d: date) -> datetime:
    """Aware local midnight that begins day ``d`` (DST-safe: built from wall time)."""
    return datetime.combine(d, time.min, tzinfo=local_tz())


def week_start(d: date) -> date:
    """Monday of the ISO week containing ``d``."""
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def prev_month_start(d: date) -> date:
    return month_start(month_start(d) - timedelta(days=1))
