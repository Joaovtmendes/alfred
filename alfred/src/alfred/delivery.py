"""Outbound-delivery guard for one inbound message (V1-23).

Problem: a handler sends its reply and only later commits. If it fails in between, the
writes roll back, the message stays ``processed = false`` and the recovery sweep re-runs
it 2-20 minutes later, so the user receives the same reply twice.

Fix: the webhook opens a capture around each handler run. ``whatsapp.send_*`` report every
successful send here. When a run fails after having sent something, the message is flagged
``reply_sent``; the retry still redoes the work (so nothing is lost) but with sending
suppressed, so the user does not get a second copy. Outside a capture (cron, scripts)
everything here is a no-op.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class _State:
    suppress: bool = False
    sent: int = 0


_state: ContextVar[_State | None] = ContextVar("delivery_state", default=None)


def begin(*, suppress: bool = False) -> _State:
    """Start a capture for one handler run; ``suppress`` turns every send into a no-op."""
    state = _State(suppress=suppress)
    _state.set(state)
    return state


def end() -> None:
    _state.set(None)


def suppressed() -> bool:
    state = _state.get()
    return bool(state and state.suppress)


def note_sent() -> None:
    state = _state.get()
    if state is not None:
        state.sent += 1
