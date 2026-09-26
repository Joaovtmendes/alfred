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


async def send_template(
    to: str,
    template_name: str,
    lang_code: str = "en",
    components: list[dict] | None = None,
) -> dict:
    """Send an approved WhatsApp Message Template.

    Args:
        to: Recipient phone in E.164 without '+' (e.g. '31612345678').
        template_name: Approved template name (e.g. 'alfred_weekly_summary').
        lang_code: BCP-47 language code matching the approved template ('en', 'pt', 'nl', 'fr', 'de').
        components: Optional list of template component objects (header/body/button params).

    Returns:
        Meta API response dict.
    """
    url = f"{_GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages"
    template: dict = {
        "name": template_name,
        "language": {"code": lang_code},
    }
    if components:
        template["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": template,
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token.get_secret_value()}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if not resp.is_success:
            logger.error(
                "whatsapp.template_send_failed",
                status=resp.status_code,
                body=resp.text,
                to=to,
                template=template_name,
            )
        resp.raise_for_status()
        data = resp.json()

    logger.info(
        "whatsapp.template_sent",
        to=to,
        template=template_name,
        lang=lang_code,
        wa_message_id=data.get("messages", [{}])[0].get("id"),
    )
    return data
