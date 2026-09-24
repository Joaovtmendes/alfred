"""Webhook signature validation (HMAC-SHA256).

Meta signs every POST with X-Hub-Signature-256: sha256=<hex>
Reference: https://developers.facebook.com/docs/messenger-platform/webhooks#validate-payloads
"""
from __future__ import annotations

import hashlib
import hmac

from fastapi import Header, HTTPException, Request, status

from alfred.settings import settings


def _expected_signature(payload: bytes) -> str:
    secret = settings.whatsapp_app_secret.get_secret_value().encode()
    digest = hmac.new(secret, payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def verify_whatsapp_signature(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
) -> None:
    """FastAPI dependency — raises 401 if signature is missing or invalid."""
    body = await request.body()
    expected = _expected_signature(body)
    if not hmac.compare_digest(expected, x_hub_signature_256):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )
