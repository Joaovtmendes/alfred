"""Day boundaries follow Europe/Amsterdam, not UTC (bug 1.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from alfred import clock
from alfred.clock import day_start, month_start, prev_month_start, to_local, week_start


class _Frozen(datetime):
    _now = datetime(2026, 9, 28, 22, 30, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls._now.astimezone(tz) if tz else cls._now


def _freeze(monkeypatch, dt: datetime) -> None:
    _Frozen._now = dt
    monkeypatch.setattr(clock, "datetime", _Frozen)


def test_after_local_midnight_is_already_tomorrow(monkeypatch) -> None:
    # 22:30 UTC in September = 00:30 next day in Amsterdam (CEST, UTC+2)
    _freeze(monkeypatch, datetime(2026, 9, 28, 22, 30, tzinfo=UTC))
    assert clock.today_local() == date(2026, 9, 29)


def test_winter_offset(monkeypatch) -> None:
    # January: CET (UTC+1) → 23:30 UTC is already the next day
    _freeze(monkeypatch, datetime(2026, 1, 10, 23, 30, tzinfo=UTC))
    assert clock.today_local() == date(2026, 1, 11)
    _freeze(monkeypatch, datetime(2026, 1, 10, 22, 30, tzinfo=UTC))
    assert clock.today_local() == date(2026, 1, 10)


def test_month_boundary(monkeypatch) -> None:
    # 30 Sep 22:30 UTC = 1 Oct 00:30 local → October, not September
    _freeze(monkeypatch, datetime(2026, 9, 30, 22, 30, tzinfo=UTC))
    assert month_start(clock.today_local()) == date(2026, 10, 1)


def test_day_start_is_local_midnight_in_utc() -> None:
    assert day_start(date(2026, 9, 29)).astimezone(UTC) == datetime(2026, 9, 28, 22, 0, tzinfo=UTC)
    assert day_start(date(2026, 1, 11)).astimezone(UTC) == datetime(2026, 1, 10, 23, 0, tzinfo=UTC)


def test_day_start_across_dst_change() -> None:
    # 2026-10-25 is a 25-hour day: CEST→CET
    a = day_start(date(2026, 10, 25)).astimezone(UTC)
    b = day_start(date(2026, 10, 26)).astimezone(UTC)
    assert (b - a).total_seconds() == 25 * 3600


def test_week_and_prev_month_helpers() -> None:
    assert week_start(date(2026, 9, 27)) == date(2026, 9, 21)  # Sunday → Monday before
    assert week_start(date(2026, 9, 28)) == date(2026, 9, 28)
    assert prev_month_start(date(2026, 1, 15)) == date(2025, 12, 1)
    assert prev_month_start(date(2026, 3, 31)) == date(2026, 2, 1)


def test_to_local_handles_naive_as_utc() -> None:
    assert to_local(datetime(2026, 9, 28, 22, 30)).date() == date(2026, 9, 29)


def test_month_and_weekday_names_follow_the_language() -> None:
    from alfred.clock import month_name, weekday_abbr

    d = date(2026, 9, 28)  # a Monday
    assert month_name(d, "pt", year=True) == "Setembro 2026"
    assert month_name(d, "nl") == "September"
    assert month_name(date(2026, 3, 1), "de") == "März"
    assert month_name(date(2026, 8, 1), "fr") == "Août"
    assert month_name(d, "xx") == "September"  # unknown language → English
    assert weekday_abbr(d, "pt") == "Seg"
    assert weekday_abbr(date(2026, 9, 26), "pt") == "Sáb"
    assert weekday_abbr(d, "nl") == "Ma"
