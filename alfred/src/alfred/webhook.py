"""WhatsApp Cloud API webhook — verification + message ingestion.

GET  /webhook/whatsapp  → Meta hub challenge (verification)
POST /webhook/whatsapp  → incoming messages (HMAC-validated, idempotent)

The POST is two-phase so Meta gets its 200 in milliseconds, not after the LLM:

1. **Ingest (in the request)** — verify HMAC, find/create the member, store the inbound
   message (``ON CONFLICT DO NOTHING`` → retries are ignored) and COMMIT.
2. **Process (background task, own DB session)** — per-member lock → SAVEPOINT →
   ``handle_inbound`` → mark the message ``processed``.

If the process dies between the two phases the message is stored with
``processed = false``; the web process re-drives it (``recover_unprocessed``, run every
minute by a task started in ``main.lifespan`` — the web service has the LLM key, the cron
service does not). ``dispatch_inbound`` claims the message row with
``FOR UPDATE SKIP LOCKED``, so two processes (a rolling deploy) never handle it twice.
"""

from __future__ import annotations

import asyncio
import datetime
import hmac
import uuid
import weakref
from dataclasses import dataclass

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response, status
from sqlalchemy import select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.db import AsyncSessionLocal, get_session
from alfred.models import Household, Member, Message
from alfred.observability import alert
from alfred.outbox import apply_status
from alfred.security import verify_whatsapp_signature
from alfred.settings import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])

# Per-member serialisation lock — prevents double-response when Meta sends
# concurrent/duplicate webhook calls for the same user.
# Weak values: a lock disappears once nobody holds or awaits it (no unbounded growth).
_MEMBER_LOCKS: weakref.WeakValueDictionary = weakref.WeakValueDictionary()

# A stored-but-unprocessed message is only re-driven inside this window: old enough that
# the original background task is certainly dead, young enough that a late answer is useful.
MAX_CONCURRENT_DISPATCH = 8  # < DB pool (5 + 10 overflow): each task holds one connection
_SLOTS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()  # event loop → semaphore
_WARNED_STALE: set[uuid.UUID] = set()  # log each abandoned message once, not every minute

RECOVER_MIN_AGE = datetime.timedelta(minutes=2)
RECOVER_MAX_AGE = datetime.timedelta(minutes=20)


def _dispatch_slot() -> asyncio.Semaphore:
    """Bounds concurrent background handlers (one semaphore per event loop)."""
    loop = asyncio.get_running_loop()
    sem = _SLOTS.get(loop)
    if sem is None:
        sem = _SLOTS[loop] = asyncio.Semaphore(MAX_CONCURRENT_DISPATCH)
    return sem


@dataclass(frozen=True)
class Inbound:
    """A stored inbound message waiting to be handled."""

    member_id: uuid.UUID
    message_id: uuid.UUID
    wa_message_id: str


def _mask_phone(phone: str) -> str:
    """Log-safe phone: keep only the last 4 digits (GDPR data minimisation)."""
    return f"***{phone[-4:]}" if phone else ""


def _get_member_lock(member_id: object) -> asyncio.Lock:
    lock = _MEMBER_LOCKS.get(member_id)
    if lock is None:
        lock = asyncio.Lock()
        _MEMBER_LOCKS[member_id] = lock
    return lock


# ---------------------------------------------------------------------------
# GET — Meta verification challenge
# ---------------------------------------------------------------------------


@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    expected = settings.whatsapp_verify_token
    # In production a missing/placeholder verify token must not be accepted: the placeholder is
    # public in the repo. (Meta only calls this when the webhook is (re)configured.)
    insecure = settings.environment == "production" and expected in ("", "dev_verify_token")
    if insecure:
        logger.error("webhook.verify_token_not_configured")
    if (
        hub_mode == "subscribe"
        and not insecure
        and hmac.compare_digest(hub_verify_token.encode(), expected.encode())
    ):
        logger.info("webhook.verified")
        return Response(content=hub_challenge, media_type="text/plain")
    logger.warning("webhook.verify_failed", mode=hub_mode)
    return Response(status_code=status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# POST — incoming messages
# ---------------------------------------------------------------------------


@router.post(
    "/whatsapp",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_whatsapp_signature)],
)
async def receive_message(
    request: Request,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> dict:
    payload: dict = await request.json()
    log = logger.bind(payload_keys=list(payload.keys()))

    # Phase 1 — store. Meta wraps messages inside entry[].changes[].value
    pending: list[Inbound] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            pending.extend(await _ingest_value(value, session, log))
            for st in value.get("statuses") or []:  # V2-18: delivery receipts
                if isinstance(st, dict) and await apply_status(session, st):
                    log.info("webhook.status_applied", status=st.get("status"))

    # Commit BEFORE answering: once Meta sees the 200 it never resends, so the
    # message must already be durable.
    await session.commit()

    # Phase 2 — process after the response has been sent, in arrival order.
    for item in pending:
        background.add_task(dispatch_inbound, item)

    # Always 200 — Meta retries on any non-2xx
    return {"status": "ok"}


async def _ingest_value(
    value: dict,
    session: AsyncSession,
    log: structlog.BoundLogger,
) -> list[Inbound]:
    """Store the new messages of one changes.value block; return those to process."""
    pending: list[Inbound] = []
    messages = value.get("messages", [])
    contacts = {c["wa_id"]: c for c in value.get("contacts", [])}

    for msg in messages:
        wa_message_id: str = msg.get("id", "")
        from_phone: str = msg.get("from", "")
        msg_type: str = msg.get("type", "unknown")

        # Only process text messages for now (M18/M19 will add image/audio).
        # Reactions, system notifications, delivery receipts in messages[] all
        # have body=None and must NOT reach handle_inbound.
        # Allow 'text' and 'interactive' (Flow nfm_reply); drop everything else.
        if msg_type not in ("text", "interactive"):
            log.info(
                "webhook.non_text_ignored",
                wa_message_id=wa_message_id,
                from_phone=_mask_phone(from_phone),
                msg_type=msg_type,
            )
            continue

        # For text messages, ensure body is non-empty.
        if msg_type == "text":
            body: str | None = msg.get("text", {}).get("body")
            if not body:
                log.warning(
                    "webhook.empty_body_ignored",
                    wa_message_id=wa_message_id,
                    from_phone=_mask_phone(from_phone),
                    msg_type=msg_type,
                )
                continue
        else:
            # Interactive (nfm_reply from WhatsApp Flow) — body is None;
            # handle_inbound inspects message.raw directly.
            body = None

        # Timestamp (Unix epoch → datetime)
        ts_raw = msg.get("timestamp")
        wa_ts = datetime.datetime.fromtimestamp(int(ts_raw), tz=datetime.UTC) if ts_raw else None

        log.info(
            "webhook.message_received",
            wa_message_id=wa_message_id,
            from_phone=_mask_phone(from_phone),
            msg_type=msg_type,
        )

        # --- Upsert household + member (auto-provision on first contact) ---
        member = await _get_or_create_member(session, from_phone, contacts)

        # --- Idempotent message insert (ON CONFLICT DO NOTHING) ---
        stmt = (
            pg_insert(Message)
            .values(
                wa_message_id=wa_message_id,
                household_id=member.household_id,
                author_id=member.id,
                direction="inbound",
                body=body,
                wa_timestamp=wa_ts,
                raw=msg,
            )
            .on_conflict_do_nothing(index_elements=["wa_message_id"])
            .returning(Message.id)
        )
        result = await session.execute(stmt)
        inserted = result.scalar_one_or_none()

        if inserted is None:
            log.info("webhook.duplicate_ignored", wa_message_id=wa_message_id)
        else:
            log.info("webhook.message_stored", message_id=str(inserted))
            pending.append(Inbound(member.id, inserted, wa_message_id))
    return pending


async def dispatch_inbound(item: Inbound) -> bool:
    """Run ``handle_inbound`` for one stored message, in its own session.

    Returns True when the handler finished. Never raises (it runs in a background task
    where an exception would just be lost); failures are logged and leave the message
    ``processed = false`` so ``recover_unprocessed`` can retry.
    """
    log = logger.bind(wa_message_id=item.wa_message_id, member_id=str(item.member_id))
    try:
        # member lock first (waiters must not hold a slot), then the concurrency slot
        async with (
            _get_member_lock(item.member_id),
            _dispatch_slot(),
            AsyncSessionLocal() as session,
        ):
            member = await session.get(Member, item.member_id)
            if member is None:
                log.info("webhook.dispatch_skipped", reason="missing_member")
                return False
            # Answer in arrival order: requests finish (and start their background task)
            # in any order, so whichever task gets the lock first takes the OLDEST unanswered
            # message of this member, not necessarily its own. Repeat until ours is done.
            attempted: set[uuid.UUID] = set()
            oldest_allowed = datetime.datetime.now(datetime.UTC) - RECOVER_MAX_AGE
            while True:
                # Claim the row: held until commit, so another process (rolling deploy, the
                # recovery sweep) skips it instead of answering the same message twice.
                stored = (
                    await session.execute(
                        select(Message)
                        .where(
                            Message.author_id == item.member_id,
                            Message.direction == "inbound",
                            Message.processed.is_(False),
                            Message.created_at >= oldest_allowed,
                            Message.id.not_in(attempted) if attempted else true(),
                        )
                        .order_by(Message.created_at.asc(), Message.id.asc())
                        .limit(1)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if stored is None:
                    # nothing left to claim: ours is done (True) or held by another worker
                    done = await session.scalar(
                        select(Message.processed).where(Message.id == item.message_id)
                    )
                    if not done:
                        log.info("webhook.dispatch_skipped", reason="missing_or_claimed")
                    return bool(done)
                attempted.add(stored.id)
                ok = await _handle_one(member, stored, session, log)
                if stored.id == item.message_id:
                    return ok
    except Exception as exc:  # DB down etc. — the message is stored; recovery retries
        log.error("webhook.dispatch_failed", error=str(exc), exc_info=True)
        return False


async def _handle_one(member, stored, session, log) -> bool:
    """Run the handler for one claimed message; commit either way. True when it finished.

    If a previous run already answered the user and then failed (``reply_sent``), this run
    redoes the work with sending suppressed so the reply is not delivered twice.
    """
    from alfred import delivery, llm_usage
    from alfred.conversation import handle_inbound

    member_id = member.id  # read now: the handler may erase the member
    message_id = stored.id
    retry_quietly = bool(stored.reply_sent)
    usage = llm_usage.begin()  # token accounting for every LLM call this message triggers
    sends = delivery.begin(suppress=retry_quietly)
    try:
        # SAVEPOINT: if the handler fails half-way, its partial writes are rolled back
        # but the inbound message stays stored.
        async with session.begin_nested():
            await handle_inbound(member, stored, session)
    except Exception as exc:
        log.error(
            "webhook.handle_inbound_failed",
            wa_message_id=stored.wa_message_id,
            error=str(exc),
            exc_info=True,
        )
        if sends.sent and not retry_quietly:
            # the user already got a reply: remember it so the retry stays silent
            await session.execute(
                update(Message).where(Message.id == message_id).values(reply_sent=True)
            )
            log.warning("webhook.failed_after_reply", message_id=str(message_id))
        await llm_usage.flush(session, member_id, usage)  # tokens were spent even if it failed
        await session.commit()  # keep whatever the savepoint left (the message)
        return False
    finally:
        delivery.end()
    if retry_quietly:
        log.info("webhook.retry_reply_suppressed", message_id=str(message_id))
    stored.processed = True
    await llm_usage.flush(session, member_id, usage)
    await session.commit()
    return True


async def recover_unprocessed(now: datetime.datetime | None = None, limit: int = 20) -> int:
    """Re-drive inbound messages that were stored but never processed.

    Covers a crash/deploy between the 200 and the end of the background task.
    Returns how many were re-driven.
    """
    now = now or datetime.datetime.now(datetime.UTC)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Message.id, Message.author_id, Message.wa_message_id)
                .where(
                    Message.direction == "inbound",
                    Message.processed.is_(False),
                    Message.author_id.is_not(None),
                    Message.created_at <= now - RECOVER_MIN_AGE,
                    Message.created_at >= now - RECOVER_MAX_AGE,
                )
                .order_by(Message.created_at.asc())
                .limit(limit)
            )
        ).all()
    for row in rows:
        logger.warning("webhook.recovering_message", wa_message_id=row.wa_message_id)
        await dispatch_inbound(Inbound(row.author_id, row.id, row.wa_message_id))

    # Messages that fell out of the window unanswered are lost to the user: say so loudly
    # (once each) so an alert can be attached to this log line.
    async with AsyncSessionLocal() as session:
        stale = (
            await session.execute(
                select(Message.id, Message.wa_message_id).where(
                    Message.direction == "inbound",
                    Message.processed.is_(False),
                    Message.created_at < now - RECOVER_MAX_AGE,
                    Message.created_at >= now - datetime.timedelta(hours=24),
                )
            )
        ).all()
    for row in stale:
        if row.id not in _WARNED_STALE:
            _WARNED_STALE.add(row.id)
            alert("webhook.stale_unprocessed_message", wa_message_id=row.wa_message_id)
    return len(rows)


RECOVER_INTERVAL_SECONDS = 60


async def recovery_loop() -> None:
    """Run ``recover_unprocessed`` forever (started by ``main.lifespan``)."""
    while True:
        await asyncio.sleep(RECOVER_INTERVAL_SECONDS)
        try:
            await recover_unprocessed()
        except Exception as exc:
            logger.error("webhook.recovery_failed", error=str(exc))


async def _get_or_create_member(
    session: AsyncSession,
    wa_phone: str,
    contacts: dict,
) -> Member:
    """Return existing member or create household + member on first contact."""
    result = await session.execute(select(Member).where(Member.wa_phone == wa_phone))
    member = result.scalar_one_or_none()
    if member:
        return member

    # New user — create a single-person household
    display_name: str | None = None
    contact = contacts.get(wa_phone)
    if contact:
        profile = contact.get("profile", {})
        display_name = profile.get("name")

    household = Household(name=display_name or wa_phone)
    session.add(household)
    await session.flush()  # get household.id

    member = Member(
        household_id=household.id,
        wa_phone=wa_phone,
        display_name=display_name,
        consent_state="pending",
    )
    session.add(member)
    await session.flush()

    log = structlog.get_logger(__name__)
    log.info(
        "webhook.new_member",
        wa_phone=_mask_phone(wa_phone),
        household_id=str(household.id),
    )
    return member
