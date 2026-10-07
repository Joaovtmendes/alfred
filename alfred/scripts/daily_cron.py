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


def due_at(time_of_day: str, day: datetime) -> datetime | None:
    """Due datetime (local, tz-aware) on ``day``'s date for "HH:MM", or None if invalid."""
    try:
        hh, mm = (int(x) for x in time_of_day.strip().split(":"))
        # fold=0: on the autumn day the ambiguous hour means its first occurrence
        return day.replace(hour=hh, minute=mm, second=0, microsecond=0, fold=0)
    except (ValueError, AttributeError):
        return None


def is_job_due(
    time_of_day: str,
    days_mask: int,
    last_sent_at: datetime | None,
    now_local: datetime,
    window: timedelta = WINDOW,
    day_of_month: int | None = None,
) -> bool:
    """Pure decision function — unit-tested in tests/test_cron.py.

    Checks today's and yesterday's occurrence, so a 23:50 reminder still fires on
    the 00:00 run. Comparisons are done in UTC so DST changes never shrink or
    stretch the window (wall-clock arithmetic breaks on the spring-forward day).
    """
    now_utc = now_local.astimezone(UTC)
    for day in (now_local, now_local - timedelta(days=1)):
        if not days_mask & weekday_bit(day):
            continue
        if day_of_month and day.day != day_of_month:
            continue
        due = due_at(time_of_day, day)
        if due is None:
            return False
        due_utc = due.astimezone(UTC)
        if due_utc <= now_utc < due_utc + window:
            return last_sent_at is None or last_sent_at.astimezone(UTC) < due_utc
    return False


def is_weekly_summary_due(now_local: datetime, interval_minutes: int = INTERVAL_MINUTES) -> bool:
    """True for exactly one cron run: Monday, [09:00, 09:00 + interval)."""
    if now_local.weekday() != 0:
        return False
    start = now_local.replace(
        hour=WEEKLY_SUMMARY_AT.hour, minute=WEEKLY_SUMMARY_AT.minute, second=0, microsecond=0
    )
    return start <= now_local < start + timedelta(minutes=interval_minutes)


def _one_line(text: str) -> str:
    """Template parameters cannot hold line breaks, tabs or runs of spaces (Meta rejects them)."""
    return " ".join(str(text).split())


async def _log_out(
    wa_phone: str, resp: dict | None, body: str, kind: str, template_name: str | None = None
) -> None:
    """V2-18 — keep a row for a proactive send so its delivery status can be shown later.

    Never raises: losing the log line must not look like a failed send.
    """
    try:
        from alfred.db import AsyncSessionLocal
        from alfred.outbox import record_outbound
        from alfred.whatsapp import _wamid

        async with AsyncSessionLocal() as session:
            await record_outbound(
                session,
                wa_phone,
                body,
                kind=kind,
                wa_message_id=_wamid(resp or {}),
                template_name=template_name,
            )
            await session.commit()
    except Exception as exc:
        logger.error("cron.outbound_log_failed", error=str(exc))


async def _purge_outbound() -> None:
    """V2-18 — outbound history older than the retention period (90 days) is deleted."""
    try:
        from alfred.db import AsyncSessionLocal
        from alfred.outbox import purge_old

        async with AsyncSessionLocal() as session:
            n = await purge_old(session)
            await session.commit()
        if n:
            logger.info("cron.outbound_purged", rows=n)
    except Exception as exc:
        logger.error("cron.outbound_purge_failed", error=str(exc))


async def send_payment_reminders(now_local: datetime) -> tuple[int, int]:
    """V2-02 — "aluguel vence em 3 dias". Returns (sent, errors).

    Inside the 24 h window after the member last wrote, a plain message goes out; outside it
    only the approved template can, and that stays off until
    ``PAYMENT_REMINDER_TEMPLATE_ENABLED`` is set. Idempotent per due date (``last_reminded_for``);
    one failed bill never stops the others.
    """
    from alfred.db import AsyncSessionLocal
    from alfred.recurring import mark_reminded, pending_reminders
    from alfred.settings import settings
    from alfred.whatsapp import send_template, send_text

    async with AsyncSessionLocal() as session:
        reminders = await pending_reminders(session, now_local)

    sent = errors = 0
    for r in reminders:
        try:
            tname = None
            if r.in_window:
                resp = await send_text(r.wa_phone, r.text)
            elif settings.payment_reminder_template_enabled:
                tname = "alfred_payment_reminder"
                resp = await send_template(
                    to=r.wa_phone,
                    template_name="alfred_payment_reminder",
                    lang_code=LANG_CODE_MAP.get(r.lang, "en"),
                    components=[
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": _one_line(r.text)}],
                        }
                    ],
                )
            else:
                logger.info("cron.payment_reminder_skipped_no_window", item_id=str(r.item_id))
                continue
        except Exception as exc:
            logger.error("cron.payment_reminder_failed", item_id=str(r.item_id), error=str(exc))
            errors += 1
            continue
        sent += 1
        await _log_out(r.wa_phone, resp, r.text, "reminder", tname)
        async with AsyncSessionLocal() as session:
            await mark_reminded(session, r.item_id, r.due)
            await session.commit()
    return sent, errors


async def send_monthly_summaries(now_local: datetime) -> tuple[int, int]:
    """V2-04 — last month's summary on day 1 (retried until day 3). Returns (sent, errors).

    Same delivery rule as the bill reminders: plain text inside the 24 h window, otherwise the
    approved template ``alfred_monthly_summary`` once ``MONTHLY_SUMMARY_TEMPLATE_ENABLED`` is on.
    """
    from alfred.db import AsyncSessionLocal
    from alfred.monthly_summary import mark_sent, pending_summaries
    from alfred.settings import settings
    from alfred.whatsapp import send_template, send_text

    async with AsyncSessionLocal() as session:
        due = await pending_summaries(session, now_local)

    sent = errors = 0
    for d in due:
        try:
            tname = None
            if d.in_window:
                resp = await send_text(d.wa_phone, d.text)
            elif settings.monthly_summary_template_enabled:
                tname = "alfred_monthly_summary"
                resp = await send_template(
                    to=d.wa_phone,
                    template_name="alfred_monthly_summary",
                    lang_code=LANG_CODE_MAP.get(d.lang, "en"),
                    components=[
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": _one_line(d.one_line)}],
                        }
                    ],
                )
            else:
                logger.info("cron.monthly_summary_waiting_for_window", member_id=str(d.member_id))
                continue
        except Exception as exc:
            logger.error("cron.monthly_summary_failed", member_id=str(d.member_id), error=str(exc))
            errors += 1
            continue
        sent += 1
        await _log_out(d.wa_phone, resp, d.text, "summary", tname)
        async with AsyncSessionLocal() as session:
            await mark_sent(session, d.member_id, datetime.now(UTC))
            await session.commit()
    return sent, errors


async def send_appointment_reminders(now_local: datetime) -> tuple[int, int]:
    """V2-06 — "dentista às 14:00" sent ``remind_before_minutes`` ahead. Returns (sent, errors).

    Plain text inside the 24 h window; outside it the approved template
    ``alfred_appointment_reminder`` (flag ``APPOINTMENT_REMINDER_TEMPLATE_ENABLED``). Once per
    appointment (``reminded_at``); a failed send is retried on the next run until it starts.
    """
    from alfred.agenda import mark_reminded, pending_reminders
    from alfred.db import AsyncSessionLocal
    from alfred.settings import settings
    from alfred.whatsapp import send_template, send_text

    async with AsyncSessionLocal() as session:
        due = await pending_reminders(session, now_local)

    sent = errors = 0
    for r in due:
        try:
            tname = None
            if r.in_window:
                resp = await send_text(r.wa_phone, r.text)
            elif settings.appointment_reminder_template_enabled:
                tname = "alfred_appointment_reminder"
                resp = await send_template(
                    to=r.wa_phone,
                    template_name="alfred_appointment_reminder",
                    lang_code=LANG_CODE_MAP.get(r.lang, "en"),
                    components=[
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": _one_line(r.text)}],
                        }
                    ],
                )
            else:
                logger.info(
                    "cron.appointment_reminder_waiting_for_window", id=str(r.appointment_id)
                )
                continue
        except Exception as exc:
            logger.error(
                "cron.appointment_reminder_failed", id=str(r.appointment_id), error=str(exc)
            )
            errors += 1
            continue
        sent += 1
        await _log_out(r.wa_phone, resp, r.text, "reminder", tname)
        async with AsyncSessionLocal() as session:
            await mark_reminded(session, r.appointment_id, datetime.now(UTC))
            await session.commit()
    return sent, errors


async def send_service_reminders(now_local: datetime) -> tuple[int, int]:
    """V2-36 — warranty ends in 30 / 7 days, cooling-off ends in 3. Returns (sent, errors).

    Plain text inside the 24 h window; outside it the approved template
    ``alfred_warranty_reminder`` (flag ``WARRANTY_REMINDER_TEMPLATE_ENABLED``). Each reminder is
    marked sent only after it went out, so a failed send is retried on the next run.
    """
    from alfred.db import AsyncSessionLocal
    from alfred.services import due_reminders, mark_sent
    from alfred.settings import settings
    from alfred.whatsapp import send_template, send_text

    async with AsyncSessionLocal() as session:
        due = await due_reminders(session, now_local.date(), now_local)

    sent = errors = 0
    for r in due:
        try:
            tname = None
            if r.in_window:
                resp = await send_text(r.wa_phone, r.text)
            elif settings.warranty_reminder_template_enabled:
                tname = "alfred_warranty_reminder"
                resp = await send_template(
                    to=r.wa_phone,
                    template_name=tname,
                    lang_code=LANG_CODE_MAP.get(r.lang, "en"),
                    components=[
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": _one_line(r.text)}],
                        }
                    ],
                )
            else:
                logger.info("cron.service_reminder_waiting_for_window", id=str(r.record_id))
                continue
        except Exception as exc:
            logger.error("cron.service_reminder_failed", id=str(r.record_id), error=str(exc))
            errors += 1
            continue
        sent += 1
        await _log_out(r.wa_phone, resp, r.text, "reminder", tname)
        async with AsyncSessionLocal() as session:
            await mark_sent(session, r.record_id, r.which)
            await session.commit()
    return sent, errors


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

    # Read everything first into plain values, then send. Each last_sent_at update
    # gets its own short session, so one failed send never breaks the rest of the run.
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    ScheduledJob.id,
                    ScheduledJob.member_id,
                    ScheduledJob.job_type,
                    ScheduledJob.time_of_day,
                    ScheduledJob.days_mask,
                    ScheduledJob.payload,
                    ScheduledJob.last_sent_at,
                    Member.wa_phone,
                    Member.language,
                )
                .join(Member, ScheduledJob.member_id == Member.id)
                .where(ScheduledJob.active.is_(True), Member.consent_state == "accepted")
            )
        ).all()
        weekly_targets = []
        if is_weekly_summary_due(now_local):
            weekly_targets = (
                await session.execute(
                    select(Member.id, Member.wa_phone, Member.language).where(
                        Member.consent_state == "accepted"
                    )
                )
            ).all()

    own_weekly = {r.member_id for r in rows if r.job_type == "weekly_summary"}

    for r in rows:
        if not is_job_due(
            r.time_of_day,
            r.days_mask,
            r.last_sent_at,
            now_local,
            day_of_month=(r.payload or {}).get("day_of_month"),
        ):
            continue
        template_name = TEMPLATE_MAP.get(r.job_type)
        if not template_name:
            logger.warning("cron.unknown_job_type", job_id=str(r.id), job_type=r.job_type)
            continue
        text = (r.payload or {}).get("text")
        components = (
            [{"type": "body", "parameters": [{"type": "text", "text": text}]}] if text else None
        )
        try:
            resp = await send_template(
                to=r.wa_phone,
                template_name=template_name,
                lang_code=LANG_CODE_MAP.get(r.language or "en", "en"),
                components=components,
            )
        except Exception as exc:
            logger.error("cron.send_failed", job_id=str(r.id), error=str(exc))
            errors += 1
            continue
        sent += 1
        await _log_out(r.wa_phone, resp, text or f"[{template_name}]", "template", template_name)
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(ScheduledJob)
                .where(ScheduledJob.id == r.id)
                .values(last_sent_at=datetime.now(UTC))
            )
            await session.commit()

    for m in weekly_targets:
        if m.id in own_weekly:
            continue  # they get it through their own job — avoid a double send
        try:
            resp = await send_template(
                to=m.wa_phone,
                template_name="alfred_weekly_summary",
                lang_code=LANG_CODE_MAP.get(m.language or "en", "en"),
            )
            sent += 1
            await _log_out(
                m.wa_phone, resp, "[alfred_weekly_summary]", "summary", "alfred_weekly_summary"
            )
        except Exception as exc:
            logger.error("cron.weekly_summary_failed", member_id=str(m.id), error=str(exc))
            errors += 1

    for extra in (
        send_payment_reminders,
        send_monthly_summaries,
        send_appointment_reminders,
        send_service_reminders,
    ):
        x_sent, x_errors = await extra(now_local)
        sent += x_sent
        errors += x_errors

    await _purge_outbound()
    logger.info("cron.done", sent=sent, errors=errors)
    return errors


if __name__ == "__main__":
    from alfred.observability import alert, init_sentry

    init_sentry("cron")
    try:
        failed = asyncio.run(run_cron())
    except Exception:
        import traceback

        traceback.print_exc()
        alert("cron.crashed")
        sys.exit(1)
    if failed:
        alert("cron.send_errors", errors=failed)
    sys.exit(1 if failed else 0)  # non-zero → Railway marks the run as failed
