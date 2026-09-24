"""WhatsApp Business Cloud API client — send messages via Meta Graph API."""
from __future__ import annotations

import httpx
import structlog

from alfred.settings import settings

logger = structlog.get_logger()

_GRAPH_URL = "https://graph.facebook.com/v19.0"


async def send_text(to: str, body: str) -> dict:
    """Send a plain-text WhatsApp message.

    Args:
        to: Recipient phone number in E.164 format without '+' (e.g. '31612345678').
        body: Message text (max 4096 chars).

    Returns:
        Meta API response dict.

    Raises:
        httpx.HTTPStatusError: on non-2xx response.
    """
    url = f"{_GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token.get_secret_value()}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if not resp.is_success:
            logger.error(
                "whatsapp.send_failed",
                status=resp.status_code,
                body=resp.text,
                to=to,
            )
        resp.raise_for_status()
        data = resp.json()

    logger.info(
        "whatsapp.message_sent",
        to=to,
        wa_message_id=data.get("messages", [{}])[0].get("id"),
    )
    return data
