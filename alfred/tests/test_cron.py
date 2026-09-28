"""M5 — reminder scheduling decisions (scripts/daily_cron.py, pure functions)."""
from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

_spec = importlib.util.spec_from_file_location(
    "daily_cron", Path(__file__).parent.parent / "scripts" / "daily_cron.py"
)
cron = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cron)

AMS = ZoneInfo("Europe/Amsterdam")
EVERY_DAY = 127
MONDAY_ONLY = 1


def at(hh: int, mm: int, day: int = 28) -> datetime:
    """2026-09-28 is a Monday."""
    return datetime(2026, 9, day, hh, mm, tzinfo=AMS)


def test_due_right_after_time() -> None:
    assert cron.is_job_due("09:00", EVERY_DAY, None, at(9, 3))


def test_not_due_before_time() -> None:
    assert not cron.is_job_due("09:00", EVERY_DAY, None, at(8, 59))


def test_not_due_after_window() -> None:
    assert not cron.is_job_due("09:00", EVERY_DAY, None, at(9, 0) + cron.WINDOW)


def test_any_time_of_day_fires_not_only_0800() -> None:
    """Regression: the old cron ran once at 08:00 UTC and dropped every other time."""
    assert cron.is_job_due("21:30", EVERY_DAY, None, at(21, 40))


def test_not_sent_twice_same_day() -> None:
    assert not cron.is_job_due("09:00", EVERY_DAY, at(9, 1), at(9, 16))


def test_sent_yesterday_is_due_again() -> None:
    yesterday = at(9, 1) - timedelta(days=1)
    assert cron.is_job_due("09:00", EVERY_DAY, yesterday, at(9, 2))


def test_days_mask_respected() -> None:
    assert cron.is_job_due("09:00", MONDAY_ONLY, None, at(9, 5, day=28))  # Monday
    assert not cron.is_job_due("09:00", MONDAY_ONLY, None, at(9, 5, day=29))  # Tuesday


def test_invalid_time_is_never_due() -> None:
    assert not cron.is_job_due("9h", EVERY_DAY, None, at(9, 5))


def test_weekly_summary_exactly_one_run_on_monday() -> None:
    assert cron.is_weekly_summary_due(at(9, 0), 15)
    assert cron.is_weekly_summary_due(at(9, 14), 15)
    assert not cron.is_weekly_summary_due(at(9, 15), 15)
    assert not cron.is_weekly_summary_due(at(9, 5, day=29), 15)  # Tuesday
