#!/usr/bin/env python3
"""M5 — Daily cron script for Alfred proactive notifications.

Run daily at 08:00 UTC (Railway cron service or external scheduler).
Queries scheduled_job for due jobs and sends directly via WhatsApp API.

Usage:
    python scripts/daily_cron.py

Railway start command for cron service:
    python /app/scripts/daily_cron.py

Environment variables: same as the main app (.env / Railway env vars).
"""
from __future__ import annotations

import asyncio
import os
import sys

# Ensure src/ is on the path when run from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


def _weekday_bit(dt: datetime) -> int:
    """Return the days_mask bit for the given datetime's weekday.

    Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64
    """
    return 1 << dt.weekday()  # weekday(): Mon=0 … Sun=6


async def run_cron() -> None:
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member, ScheduledJob
    from alfred.whatsapp import send_template
    from sqlalchemy import select, update

    now_utc = datetime.now(timezone.utc)
    today_bit = _weekday_bit(now_utc)
    current_time = now_utc.strftime("%H:%M")

    logger.info(
        "cron.daily_start",
        utc=now_utc.isoformat(),
        weekday=now_utc.strftime("%A"),
        time=current_time,
    )

    template_map = {
        "weekly_summary": "alfred_weekly_summary",
        "medication_reminder": "alfred_medication_reminder",
        "goal_checkin": "alfred_goal_checkin",
        "workout_reminder": "alfred_workout_reminder",
    }

    lang_code_map = {"pt": "pt_BR", "nl": "nl", "en": "en", "fr": "fr", "de": "de"}

    sent = 0
    errors = 0

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ScheduledJob, Member)
            .join(Member, ScheduledJob.member_id == Member.id)
            .where(
                ScheduledJob.active == True,  # noqa: E712
                Member.consent_state == "accepted",
            )
        )
        rows = result.all()

    for job, member in rows:
        # Check day mask
        if not (job.days_mask & today_bit):
            continue
        # Check time window — fire if within the same UTC hour:minute
        if job.time_of_day != current_time:
            continue

        template_name = template_map.get(job.job_type)
        if not template_name:
            logger.warning("cron.unknown_job_type", job_id=str(job.id), job_type=job.job_type)
            continue

        lang = member.language or "en"
        lang_code = lang_code_map.get(lang, "en")
        payload = job.payload or {}

        components = None
        if payload.get("text"):
            components = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": payload["text"]}],
                }
            ]

        try:
            await send_template(
                to=member.wa_phone,
                template_name=template_name,
                lang_code=lang_code,
                components=components,
            )
            # Update last_sent_at
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(ScheduledJob)
                    .where(ScheduledJob.id == job.id)
                    .values(last_sent_at=datetime.now(timezone.utc))
                )
                await session.commit()
            sent += 1
        except Exception as exc:
            logger.error(
                "cron.send_failed",
                job_id=str(job.id),
                member_id=str(member.id),
                error=str(exc),
            )
            errors += 1

    # Weekly summary: always send to ALL accepted members on Mondays at 08:00 UTC
    # (independent of scheduled_job table — it's a platform-level feature)
    if now_utc.weekday() == 0 and current_time == "08:00":
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Member).where(Member.consent_state == "accepted")
            )
            members = result.scalars().all()

        for member in members:
            lang = member.language or "en"
            lang_code = lang_code_map.get(lang, "en")
            try:
                await send_template(
                    to=member.wa_phone,
                    template_name="alfred_weekly_summary",
                    lang_code=lang_code,
                )
                sent += 1
            except Exception as exc:
                logger.error(
                    "cron.weekly_summary_failed",
                    member_id=str(member.id),
                    error=str(exc),
                )
                errors += 1

    logger.info("cron.daily_done", sent=sent, errors=errors)


if __name__ == "__main__":
    asyncio.run(run_cron())
