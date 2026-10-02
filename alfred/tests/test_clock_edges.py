"""Calendar edge cases the panel depends on (everything goes through ``alfred.clock``)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from alfred.clock import day_start, month_start, prev_month_start, week_start

AMS = ZoneInfo("Europe/Amsterdam")


def test_first_minutes_of_a_month_belong_to_the_local_month() -> None:
    utc = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)  # 00:30 on 1 Nov in Amsterdam
    assert utc.astimezone(AMS).date() == date(2026, 11, 1)
    assert month_start(utc.astimezone(AMS).date()) == date(2026, 11, 1)


def test_year_rollover_and_leap_day() -> None:
    assert prev_month_start(date(2027, 1, 15)) == date(2026, 12, 1)
    assert month_start(date(2026, 12, 31) + timedelta(days=1)) == date(2027, 1, 1)
    assert date(2028, 2, 29) + timedelta(days=1) == date(2028, 3, 1)
    assert date(2027, 2, 28) + timedelta(days=1) == date(2027, 3, 1)
    assert week_start(date(2026, 10, 2)) == date(2026, 9, 28)  # Friday -> Monday


@pytest.mark.parametrize(
    ("d", "hours"), [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25), (date(2026, 10, 2), 24)]
)
def test_dst_days_have_the_right_length(d, hours) -> None:
    span = day_start(d + timedelta(days=1)).astimezone(UTC) - day_start(d).astimezone(UTC)
    assert span.total_seconds() / 3600 == hours
