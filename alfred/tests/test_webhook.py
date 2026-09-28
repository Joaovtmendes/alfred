"""Tests for webhook verification and signature validation."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from alfred.main import app
from alfred.settings import settings


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def _sign(payload: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# --- GET verification ---


async def test_webhook_verify_valid(client: AsyncClient) -> None:
    resp = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "test_challenge_123",
        },
    )
    assert resp.status_code == 200
    assert resp.text == "test_challenge_123"


async def test_webhook_verify_wrong_token(client: AsyncClient) -> None:
    resp = await client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "xyz",
        },
    )
    assert resp.status_code == 403


# --- POST signature validation ---


async def test_webhook_post_missing_signature(client: AsyncClient) -> None:
    resp = await client.post(
        "/webhook/whatsapp",
        content=b'{"entry":[]}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 401


async def test_webhook_post_wrong_signature(client: AsyncClient) -> None:
    body = b'{"entry":[]}'
    resp = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": "sha256=badbadbad",
        },
    )
    assert resp.status_code == 401


async def test_webhook_post_valid_signature_empty_payload(
    client: AsyncClient,
) -> None:
    body = json.dumps({"entry": []}).encode()
    sig = _sign(body, settings.whatsapp_app_secret.get_secret_value())
    resp = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sig,
        },
    )
    # 200 even with no messages — Meta expects always-200
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- fail-closed when the app secret is missing ---


async def test_webhook_rejects_everything_without_app_secret(
    client: AsyncClient, monkeypatch
) -> None:
    """An empty secret must not turn into 'any HMAC with an empty key is valid'."""
    from pydantic import SecretStr

    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(""))
    body = json.dumps({"entry": []}).encode()
    resp = await client.post(
        "/webhook/whatsapp",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": _sign(body, "")},
    )
    assert resp.status_code == 401


def test_phone_is_masked_in_logs() -> None:
    from alfred.webhook import _mask_phone

    assert _mask_phone("31631152144") == "***2144"
    assert _mask_phone("") == ""
