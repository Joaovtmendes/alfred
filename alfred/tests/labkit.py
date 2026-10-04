"""Shared harness for DB-backed router tests: a real member + ``say()``.

Only the WhatsApp sender and the LLM calls are patched; router, SQL and models are real.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from alfred.conversation import handle_inbound
from alfred.db import AsyncSessionLocal, engine
from alfred.models import Base, Household, Member, Message
from tests.conftest import make_message


class Lab:
    """A member in a real DB plus a ``say()`` that runs one inbound message."""

    def __init__(self, member_id: uuid.UUID, household_id: uuid.UUID, phone: str) -> None:
        self.member_id, self.household_id, self.phone = member_id, household_id, phone
        self.sent: list[str] = []
        self.buttons: list[list[tuple[str, str]]] = []  # buttons of each reply (may be empty)
        self.cta: list[tuple[str, str]] = []  # (url, button text) of each call-to-action reply
        self.expense = AsyncMock(return_value=None)
        self.llm_reply = AsyncMock(return_value="[llm]")
        self.classify = AsyncMock(return_value=None)
        self.multi = AsyncMock(return_value=[])
        self.habit = AsyncMock(return_value=None)
        self.health = AsyncMock(return_value=None)
        self.workout = AsyncMock(return_value=None)

    async def say(self, body: str) -> str:
        """Send one message; returns the reply text."""
        return await self._run(make_message(body))

    async def tap(self, button_id: str, title: str = "btn") -> str:
        """Tap a reply button (``interactive.button_reply``); returns the reply text."""
        raw = {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": button_id, "title": title},
            },
        }
        return await self._run(make_message(body=None, raw=raw))

    async def doc(self, filename: str, data: bytes | str | None, mime: str = "text/csv") -> str:
        """Send a document (a statement file); ``data`` is what the media download returns."""
        raw = {
            "type": "document",
            "document": {"id": "media123", "filename": filename, "mime_type": mime},
        }
        payload = data.encode() if isinstance(data, str) else data
        with patch("alfred.statement.download_media", AsyncMock(return_value=payload)):
            return await self._run(make_message(body=None, raw=raw))

    async def _run(self, message) -> str:
        before = len(self.sent)

        async def fake_send(to: str, text: str) -> dict:
            self.sent.append(text)
            self.buttons.append([])
            return {}

        async def fake_buttons(to: str, text: str, buttons: list) -> dict:
            self.sent.append(text)
            self.buttons.append(list(buttons))
            return {}

        async def fake_cta(to: str, body: str, display_text: str, url: str) -> dict:
            self.sent.append(body)
            self.buttons.append([])
            self.cta.append((url, display_text))
            return {}

        with (
            patch("alfred.conversation.send_text", fake_send),
            patch("alfred.conversation.send_cta_url", fake_cta),
            patch("alfred.conversation.send_buttons", fake_buttons),
            patch("alfred.conversation.extract_expense", self.expense),
            patch("alfred.conversation.extract_expenses_multi", self.multi),
            patch("alfred.conversation.extract_habit", self.habit),
            patch("alfred.conversation.extract_health_log", self.health),
            patch("alfred.conversation.extract_workout", self.workout),
            patch("alfred.conversation.classify_query", self.classify),
            patch("alfred.conversation.generate_reply", self.llm_reply),
        ):
            async with AsyncSessionLocal() as s:
                member = await s.get(Member, self.member_id)
                await handle_inbound(member, message, s)
                await s.commit()
        assert len(self.sent) == before + 1, f"expected exactly one reply, got {self.sent[before:]}"
        return self.sent[-1]

    async def scalar(self, stmt):
        async with AsyncSessionLocal() as s:
            return (await s.execute(stmt)).scalar()

    async def rows(self, stmt) -> list:
        async with AsyncSessionLocal() as s:
            return list((await s.execute(stmt)).all())

    async def add(self, *rows) -> None:
        async with AsyncSessionLocal() as s:
            s.add_all(rows)
            await s.commit()


async def _make_lab(language: str = "pt") -> Lab:
    phone = "3160" + uuid.uuid4().hex[:7]
    async with AsyncSessionLocal() as s:
        hh = Household(name="bugs")
        s.add(hh)
        await s.flush()
        m = Member(household_id=hh.id, wa_phone=phone, consent_state="accepted", language=language)
        s.add(m)
        await s.commit()
        return Lab(m.id, hh.id, phone)


async def _drop_lab(lab: Lab) -> None:
    async with AsyncSessionLocal() as s:
        # every table that hangs off the member, children first
        for table in reversed(Base.metadata.sorted_tables):
            if "member_id" in table.c and table.name != "member":
                await s.execute(delete(table).where(table.c.member_id == lab.member_id))
        await s.execute(delete(Message).where(Message.household_id == lab.household_id))
        await s.execute(delete(Member).where(Member.id == lab.member_id))
        await s.execute(delete(Household).where(Household.id == lab.household_id))
        await s.commit()


@pytest.fixture
async def lab():
    await engine.dispose()  # each test runs in its own event loop
    lab = await _make_lab()
    yield lab
    await _drop_lab(lab)
    await engine.dispose()  # leave no pooled connection bound to this test's loop


@pytest.fixture
async def lab2(lab):
    """A second member (the partner), in the same test loop as ``lab``."""
    other = await _make_lab()
    yield other
    await _drop_lab(other)
