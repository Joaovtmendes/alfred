"""WhatsApp Cloud API webhook — verification + message ingestion.

GET  /webhook/whatsapp  → Meta hub challenge (verification)
POST /webhook/whatsapp  → incoming messages (HMAC-validated, idempotent)
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.db import get_session
from alfred.models import Household, Member, Message
from alfred.security import verify_whatsapp_signature
from alfred.settings import settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhook", tags=["webhook"])


# ---------------------------------------------------------------------------
# GET — Meta verification challenge
# ---------------------------------------------------------------------------

@router.get("/whatsapp")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
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
    session: AsyncSession = Depends(get_session),
) -> dict:
    payload: dict = await request.json()
    log = logger.bind(payload_keys=list(payload.keys()))

    # Meta wraps messages inside entry[].changes[].value
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            await _process_value(value, session, log)

    # Always return 200 — Meta retries on any non-2xx
    return {"status": "ok"}


async def _process_value(
    value: dict,
    session: AsyncSession,
    log: structlog.BoundLogger,
) -> None:
    """Process one changes.value block — may contain multiple messages."""
    messages = value.get("messages", [])
    contacts = {c["wa_id"]: c for c in value.get("contacts", [])}

    for msg in messages:
        wa_message_id: str = msg.get("id", "")
        from_phone: str = msg.get("from", "")
        msg_type: str = msg.get("type", "unknown")

        # Resolve body text (text messages only for M1)
        body: str | None = None
        if msg_type == "text":
            body = msg.get("text", {}).get("body")

        # Timestamp (Unix epoch → datetime)
        import datetime
        ts_raw = msg.get("timestamp")
        wa_ts = (
            datetime.datetime.fromtimestamp(int(ts_raw), tz=datetime.timezone.utc)
            if ts_raw
            else None
        )

        log.info(
            "webhook.message_received",
            wa_message_id=wa_message_id,
            from_phone=from_phone,
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
            # Fetch stored message and dispatch to conversation handler
            from sqlalchemy import select as sa_select
            from alfred.conversation import handle_inbound
            msg_result = await session.execute(
                sa_select(Message).where(Message.id == inserted)
            )
            stored_msg = msg_result.scalar_one()
            try:
                await handle_inbound(member, stored_msg, session)
            except Exception as exc:
                log.error(
                    "webhook.handle_inbound_failed",
                    error=str(exc),
                    wa_message_id=wa_message_id,
                    exc_info=True,
                )


async def _get_or_create_member(
    session: AsyncSession,
    wa_phone: str,
    contacts: dict,
) -> Member:
    """Return existing member or create household + member on first contact."""
    result = await session.execute(
        select(Member).where(Member.wa_phone == wa_phone)
    )
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
        wa_phone=wa_phone,
        household_id=str(household.id),
    )
    return member
