"""WhatsApp Business Cloud API client — send messages via Meta Graph API."""

from __future__ import annotations

import httpx
import structlog

from alfred import delivery
from alfred.settings import settings

logger = structlog.get_logger()

# v19.0 expired on 2026-05-21 (Meta silently upgraded calls). Pin a supported version;
# check https://developers.facebook.com/docs/graph-api/changelog/versions/ yearly.
_GRAPH_URL = f"https://graph.facebook.com/{settings.graph_api_version}"


def _wamid(data: dict) -> str | None:
    msgs = data.get("messages") or [{}]
    return msgs[0].get("id") if isinstance(msgs[0], dict) else None


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
    if delivery.suppressed():
        logger.info("whatsapp.send_suppressed", to=to)
        return {"suppressed": True}
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
    delivery.note_sent(_wamid(data))
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
        lang_code: Language code of the approved template ('en', 'pt_BR', 'nl', 'fr', 'de').
        components: Optional list of template component objects (header/body/button params).

    Returns:
        Meta API response dict.
    """
    if delivery.suppressed():
        logger.info("whatsapp.send_suppressed", to=to)
        return {"suppressed": True}
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
    delivery.note_sent(_wamid(data))
    return data


async def send_buttons(to: str, body: str, buttons: list[tuple[str, str]]) -> dict:
    """Send a message with up to 3 quick-reply buttons.

    Args:
        to: Recipient phone in E.164 without '+'.
        body: Message text (max 1024 chars for interactive messages).
        buttons: ``(id, title)`` pairs. Meta limits: 3 buttons, id <= 256 chars,
            title <= 20 chars. The id comes back in ``interactive.button_reply.id``.

    Falls back to a plain text message when Meta rejects the interactive payload, so a
    button problem never loses the confirmation itself.
    """
    if delivery.suppressed():
        logger.info("whatsapp.send_suppressed", to=to)
        return {"suppressed": True}
    url = f"{_GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": bid[:256], "title": title[:20]}}
                    for bid, title in buttons[:3]
                ]
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token.get_secret_value()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if not resp.is_success:
                logger.error(
                    "whatsapp.buttons_send_failed",
                    status=resp.status_code,
                    body=resp.text,
                    to=to,
                )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return await send_text(to, body)

    logger.info(
        "whatsapp.buttons_sent",
        to=to,
        wa_message_id=data.get("messages", [{}])[0].get("id"),
    )
    delivery.note_sent(_wamid(data))
    return data


async def send_cta_url(to: str, body: str, display_text: str, url: str) -> dict:
    """Send a message with one call-to-action button that opens ``url``.

    Meta limits: ``display_text`` <= 20 characters, body <= 1024. Works only inside the 24-hour
    window (a reply to the member's own message always is). Falls back to plain text with the
    link in it when Meta rejects the interactive payload, so a button problem never loses the
    link. The link is a secret: it is never logged.
    """
    if delivery.suppressed():
        logger.info("whatsapp.send_suppressed", to=to)
        return {"suppressed": True}
    endpoint = f"{_GRAPH_URL}/{settings.whatsapp_phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": body[:1024]},
            "action": {
                "name": "cta_url",
                "parameters": {"display_text": display_text[:20], "url": url},
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token.get_secret_value()}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(endpoint, json=payload, headers=headers)
            if not resp.is_success:
                logger.error("whatsapp.cta_send_failed", status=resp.status_code, to=to)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError:
        return await send_text(to, f"{body}\n{url}")

    logger.info("whatsapp.cta_sent", to=to, wa_message_id=data.get("messages", [{}])[0].get("id"))
    delivery.note_sent(_wamid(data))
    return data
