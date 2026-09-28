#!/usr/bin/env python3
"""M5 — Proactive notifications cron (reminders + weekly summary).

Runs every ``CRON_INTERVAL_MINUTES`` (default 15) as a Railway cron service:

    Start command:  python /app/scripts/daily_cron.py
    Cron schedule:  */15 * * * *

Rules
-----
* Reminder times (``ScheduledJob.time_of_day``, "HH:MM") are the user's local
  time in ``settings.timezone`` (Europe/Amsterdam), exactly as they typed them.
* A job is sent once per day: when its due time has passed within the window
  and ``last_sent_at`` is older than today's due time (idempotent re-runs).
* The platform weekly summary goes out on Mondays at 09:00 local time, except
  to members who already have their own ``weekly_summary`` job.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Ensure src/ is on the path when run from the repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import structlog  # noqa: E402

logger = structlog.get_logger()

INTERVAL_MINUTES = int(os.environ.get("CRON_INTERVAL_MINUTES", "15"))
# Window = one interval + grace, so a late cron start never drops a job.
# last_sent_at makes the overlap safe (no double sends).
WINDOW = timedelta(minutes=INTERVAL_MINUTES + 10)
WEEKLY_SUMMARY_AT = time(9, 0)  # Mondays, local time

TEMPLATE_MAP = {
    "weekly_summary": "alfred_weekly_summary",
    "medication_reminder": "alfred_medication_reminder",
    "goal_checkin": "alfred_goal_checkin",
    "workout_reminder": "alfred_workout_reminder",
}
LANG_CODE_MAP = {"pt": "pt_BR", "nl": "nl", "en": "en", "fr": "fr", "de": "de"}


def weekday_bit(dt: datetime) -> int:
    """days_mask bit for dt's weekday: Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64."""
    return 1 << dt.weekday()


def due_at(time_of_day: str, now_local: datetime) -> datetime | None:
    """Today's due datetime (local, tz-aware) for an "HH:MM" string, or None if invalid."""
    try:
        hh, mm = (int(x) for x in time_of_day.strip().split(":"))
        return now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (ValueError, AttributeError):
        return None


def is_job_due(
    time_of_day: str,
    days_mask: int,
    last_sent_at: datetime | None,
    now_local: datetime,
    window: timedelta = WINDOW,
) -> bool:
    """Pure decision function — unit-tested in tests/test_cron.py."""
    if not days_mask & weekday_bit(now_local):
        return False
    due = due_at(time_of_day, now_local)
    if due is None or not (due <= now_local < due + window):
        return False
    return last_sent_at is None or last_sent_at < due


def is_weekly_summary_due(now_local: datetime, interval_minutes: int = INTERVAL_MINUTES) -> bool:
    """True for exactly one cron run: Monday, [09:00, 09:00 + interval)."""
    if now_local.weekday() != 0:
        return False
    start = now_local.replace(
        hour=WEEKLY_SUMMARY_AT.hour, minute=WEEKLY_SUMMARY_AT.minute, second=0, microsecond=0
    )
    return start <= now_local < start + timedelta(minutes=interval_minutes)


async def run_cron() -> int:
    """Send due notifications. Returns the number of failed sends."""
    from sqlalchemy import select, update

    from alfred.db import AsyncSessionLocal
    from alfred.models import Member, ScheduledJob
    from alfred.settings import settings
    from alfred.whatsapp import send_template

    tz = ZoneInfo(settings.timezone)
    now_local = datetime.now(UTC).astimezone(tz)
    logger.info("cron.start", local=now_local.isoformat(), interval_min=INTERVAL_MINUTES)

    sent = errors = 0

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(ScheduledJob, Member)
                .join(Member, ScheduledJob.member_id == Member.id)
                .where(ScheduledJob.active.is_(True), Member.consent_state == "accepted")
            )
        ).all()

        own_weekly = {job.member_id for job, _ in rows if job.job_type == "weekly_summary"}

        for job, member in rows:
            last = job.last_sent_at.astimezone(tz) if job.last_sent_at else None
            if not is_job_due(job.time_of_day, job.days_mask, last, now_local):
                continue
            template_name = TEMPLATE_MAP.get(job.job_type)
            if not template_name:
                logger.warning("cron.unknown_job_type", job_id=str(job.id), job_type=job.job_type)
                continue

            payload = job.payload or {}
            components = None
            if payload.get("text"):
                components = [
                    {"type": "body", "parameters": [{"type": "text", "text": payload["text"]}]}
                ]
            try:
                await send_template(
                    to=member.wa_phone,
                    template_name=template_name,
                    lang_code=LANG_CODE_MAP.get(member.language or "en", "en"),
                    components=components,
                )
                await session.execute(
                    update(ScheduledJob)
                    .where(ScheduledJob.id == job.id)
                    .values(last_sent_at=datetime.now(UTC))
                )
                await session.commit()
                sent += 1
            except Exception as exc:
                await session.rollback()
                logger.error("cron.send_failed", job_id=str(job.id), error=str(exc))
                errors += 1

        if is_weekly_summary_due(now_local):
            members = (
                (await session.execute(select(Member).where(Member.consent_state == "accepted")))
                .scalars()
                .all()
            )
            for member in members:
                if member.id in own_weekly:
                    continue  # they get it through their own job — avoid a double send
                try:
                    await send_template(
                        to=member.wa_phone,
                        template_name="alfred_weekly_summary",
                        lang_code=LANG_CODE_MAP.get(member.language or "en", "en"),
                    )
                    sent += 1
                except Exception as exc:
                    logger.error(
                        "cron.weekly_summary_failed", member_id=str(member.id), error=str(exc)
                    )
                    errors += 1

    logger.info("cron.done", sent=sent, errors=errors)
    return errors


if __name__ == "__main__":
    try:
        asyncio.run(run_cron())
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
