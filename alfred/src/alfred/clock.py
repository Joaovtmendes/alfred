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


# ── Localised date labels ─────────────────────────────────────────────────────
# ``strftime("%B")`` follows the server's C locale (always English), so a
# Portuguese user used to read "September". These tables replace it.

_MONTHS: dict[str, tuple[str, ...]] = {
    "pt": (
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ),
    "nl": (
        "januari", "februari", "maart", "april", "mei", "juni",
        "juli", "augustus", "september", "oktober", "november", "december",
    ),
    "en": (
        "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ),
    "fr": (
        "janvier", "février", "mars", "avril", "mai", "juin",
        "juillet", "août", "septembre", "octobre", "novembre", "décembre",
    ),
    "de": (
        "januar", "februar", "märz", "april", "mai", "juni",
        "juli", "august", "september", "oktober", "november", "dezember",
    ),
}  # fmt: skip
_WEEKDAYS: dict[str, tuple[str, ...]] = {  # Monday first, like date.weekday()
    "pt": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
    "nl": ("ma", "di", "wo", "do", "vr", "za", "zo"),
    "en": ("mon", "tue", "wed", "thu", "fri", "sat", "sun"),
    "fr": ("lun", "mar", "mer", "jeu", "ven", "sam", "dim"),
    "de": ("mo", "di", "mi", "do", "fr", "sa", "so"),
}  # fmt: skip


def month_name(d: date, lang: str, *, year: bool = False) -> str:
    """'Setembro' / 'Setembro 2026' in the user's language (English fallback)."""
    name = _MONTHS.get(lang, _MONTHS["en"])[d.month - 1].capitalize()
    return f"{name} {d.year}" if year else name


def weekday_abbr(d: date, lang: str) -> str:
    """'Seg', 'Ma', 'Mon'… (English fallback)."""
    return _WEEKDAYS.get(lang, _WEEKDAYS["en"])[d.weekday()].capitalize()
