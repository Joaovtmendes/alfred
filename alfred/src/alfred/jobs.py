"""M5 — Procrastinate job definitions for proactive notifications.

The Procrastinate app uses the same PostgreSQL database as Alfred.
The daily cron script enqueues tasks; the worker processes them.

Architecture:
    daily_cron.py  →  enqueue tasks via procrastinate  →  worker.py
                                                               ↓
                                                       send_template() / send_text()
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import procrastinate
import structlog

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# App — created lazily to avoid import-time DB connections
# ---------------------------------------------------------------------------

def make_app(database_url: str) -> procrastinate.App:
    """Build a Procrastinate App wired to the given PostgreSQL URL.

    Converts asyncpg URLs to the psycopg2 synchronous connector that
    Procrastinate's default connector expects.
    """
    sync_url = (
        database_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgres://", "postgresql://")
    )
    connector = procrastinate.SyncPsycopg2Connector.from_dsn(sync_url)
    return procrastinate.App(connector=connector)


# Module-level app — populated at worker/cron startup via init_app()
_app: procrastinate.App | None = None


def get_app() -> procrastinate.App:
    if _app is None:
        raise RuntimeError("Call init_app() before using jobs.get_app()")
    return _app


def init_app(database_url: str) -> procrastinate.App:
    global _app
    _app = make_app(database_url)
    return _app


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def _get_task_app() -> procrastinate.App:
    """Deferred app accessor used by task decorators at call time."""
    return get_app()


# We define tasks as plain async functions and register them at call time
# so the module can be imported without a live DB connection.

async def _send_weekly_summary_impl(member_id: str) -> None:
    """Send weekly expense summary to one member via template."""
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member
    from alfred.whatsapp import send_template
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()
        if not member or member.consent_state != "accepted":
            logger.info("jobs.weekly_summary_skip", member_id=member_id, reason="no consent")
            return

        lang_map = {"pt": "pt", "nl": "nl", "en": "en", "fr": "fr", "de": "de"}
        lang_code = lang_map.get(member.language or "en", "en")

        await send_template(
            to=member.wa_phone,
            template_name="alfred_weekly_summary",
            lang_code=lang_code,
        )
        logger.info("jobs.weekly_summary_sent", member_id=member_id, lang=lang_code)


async def _send_reminder_impl(
    member_id: str,
    job_type: str,
    payload: dict | None = None,
) -> None:
    """Send a reminder notification (medication, goal, workout)."""
    from alfred.db import AsyncSessionLocal
    from alfred.models import Member
    from alfred.whatsapp import send_template
    from sqlalchemy import select

    template_map = {
        "medication_reminder": "alfred_medication_reminder",
        "goal_checkin": "alfred_goal_checkin",
        "workout_reminder": "alfred_workout_reminder",
    }
    template_name = template_map.get(job_type, "alfred_medication_reminder")

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Member).where(Member.id == uuid.UUID(member_id))
        )
        member = result.scalar_one_or_none()
        if not member or member.consent_state != "accepted":
            logger.info("jobs.reminder_skip", member_id=member_id, reason="no consent")
            return

        lang_map = {"pt": "pt", "nl": "nl", "en": "en", "fr": "fr", "de": "de"}
        lang_code = lang_map.get(member.language or "en", "en")

        # Optional body parameter with custom reminder text
        components = None
        if payload and payload.get("text"):
            components = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": payload["text"]}],
                }
            ]

        await send_template(
            to=member.wa_phone,
            template_name=template_name,
            lang_code=lang_code,
            components=components,
        )
        logger.info(
            "jobs.reminder_sent",
            member_id=member_id,
            job_type=job_type,
            lang=lang_code,
        )


# ---------------------------------------------------------------------------
# Public enqueue helpers (called by daily_cron.py)
# ---------------------------------------------------------------------------

def enqueue_weekly_summary(member_id: str) -> None:
    """Enqueue a weekly summary job for one member (sync, for cron scripts)."""
    app = get_app()
    with app.open():
        app.configure_task(
            "alfred.jobs.send_weekly_summary",
            queue="default",
        ).defer(member_id=member_id)


def enqueue_reminder(member_id: str, job_type: str, payload: dict | None = None) -> None:
    """Enqueue a reminder job for one member (sync, for cron scripts)."""
    app = get_app()
    with app.open():
        app.configure_task(
            "alfred.jobs.send_reminder",
            queue="default",
        ).defer(member_id=member_id, job_type=job_type, payload=payload)


# ---------------------------------------------------------------------------
# Async task runner — used by the worker
# ---------------------------------------------------------------------------

async def run_send_weekly_summary(member_id: str) -> None:
    await _send_weekly_summary_impl(member_id)


async def run_send_reminder(
    member_id: str, job_type: str, payload: dict | None = None
) -> None:
    await _send_reminder_impl(member_id, job_type, payload)
