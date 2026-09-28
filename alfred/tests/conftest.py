"""Shared test fixtures.

Conventions
-----------
* Unit tests use plain ``SimpleNamespace`` objects instead of ORM rows and a
  ``MagicMock`` session — no database needed.
* Never call real external services: patch ``alfred.conversation.send_text``
  and the ``alfred.llm`` functions used by the code under test.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from alfred.main import app
from alfred.settings import settings

TEST_APP_SECRET = "test-app-secret"


@pytest.fixture(autouse=True)
def _test_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic secrets for every test (the webhook fails closed without one)."""
    monkeypatch.setattr(settings, "whatsapp_app_secret", SecretStr(TEST_APP_SECRET))
    monkeypatch.setattr(settings, "whatsapp_verify_token", "test-verify-token")


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the FastAPI app (no network)."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


def make_member(consent_state: str = "accepted", language: str = "pt", **kw: Any) -> SimpleNamespace:
    """A Member-like object with every attribute handle_inbound reads."""
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "household_id": uuid.uuid4(),
        "wa_phone": "31600000001",
        "consent_state": consent_state,
        "display_name": "Test",
        "preferred_name": None,
        "language": language,
        "disclosure_accepted_at": None,
        "disclosure_version": None,
        "dashboard_token": None,
    }
    fields.update(kw)
    return SimpleNamespace(**fields)


def make_message(body: str = "olá", **kw: Any) -> SimpleNamespace:
    """A Message-like inbound text message."""
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "body": body,
        "direction": "inbound",
        "wa_message_id": "wamid.test",
        "wa_timestamp": datetime.now(timezone.utc),
        "raw": {"type": "text"},
    }
    fields.update(kw)
    return SimpleNamespace(**fields)


def make_session(records: list[Any] | None = None) -> MagicMock:
    """A session whose every query returns ``records`` (default: empty)."""
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = records or []
    result.scalar_one_or_none.return_value = None
    result.scalar.return_value = 0
    result.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    session.delete = AsyncMock()
    return session
