"""Per-message LLM token accounting (V1-21).

The extractors in ``llm.py`` know nothing about members or sessions. The webhook opens a
capture around ``handle_inbound``; every LLM call made meanwhile reports its token counts
here, and the webhook writes them in the same session afterwards. Outside a capture
(scripts, cron) ``record`` is a no-op. No text is ever stored.
"""

from __future__ import annotations

import uuid
from contextvars import ContextVar
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alfred.models import LlmUsage

_buffer: ContextVar[list[dict[str, Any]] | None] = ContextVar("llm_usage_buffer", default=None)


def begin() -> list[dict[str, Any]]:
    """Start collecting usage for the current message; returns the (shared) list."""
    buf: list[dict[str, Any]] = []
    _buffer.set(buf)
    return buf


def record(purpose: str, model: str, response: Any) -> None:
    """Note one call's tokens. Never raises: accounting must not break a reply."""
    buf = _buffer.get()
    if buf is None:
        return
    try:
        usage = response.usage
        buf.append(
            {
                "purpose": purpose,
                "model": str(model)[:80],
                "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            }
        )
    except Exception:  # noqa: BLE001 - mocks/odd responses; accounting is best-effort
        return


async def flush(session: AsyncSession, member_id: uuid.UUID, buf: list[dict[str, Any]]) -> int:
    """Write the collected rows through ``session`` (the caller commits). Returns how many.

    Runs in a SAVEPOINT: if the member was erased meanwhile (FK violation) only the
    accounting is lost, never the message that was being processed.
    """
    n = len(buf)
    try:
        if buf:
            async with session.begin_nested():
                for item in buf:
                    session.add(LlmUsage(member_id=member_id, **item))
    except Exception:  # noqa: BLE001 - accounting is best-effort
        n = 0
    buf.clear()
    _buffer.set(None)
    return n
