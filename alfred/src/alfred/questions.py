# ruff: noqa: E501
"""Follow-up questions ("Vence quando?", "Criar a categoria Pets?"); not followup.py, that is analysis.

One open question per member, kept in ``pending_action`` (kind ``followup``, 15 minutes). A new
question replaces the old one. A button carries the action id (``fu:<id>:<choice>``); typed text
answers only when it matches the strict pattern of that question type, and any other message
discards the question and goes on as usual, so a stale question never swallows a new message.

Each module that asks a question registers two things at import time:

* ``PARSERS[type](text, data) -> str | None``: the typed answer, as a choice, or None;
* ``HANDLERS[type](session, member, lang, data, choice, today) -> Reply``: what the choice does.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.couple import Reply
from alfred.models import Member, PendingAction

KIND = "followup"
TTL_MINUTES = 15

STRINGS: dict[str, dict[str, str]] = {
    "fu_gone": {
        "pt": "Essa pergunta já foi respondida ou expirou. Pode me dizer de novo o que quer fazer.",
        "nl": "Die vraag is al beantwoord of verlopen. Zeg gerust opnieuw wat je wilt doen.",
        "en": "That question was already answered or has expired. Just tell me again what you want.",
        "fr": "Cette question a déjà reçu une réponse ou a expiré. Dis-moi à nouveau ce que tu veux.",
        "de": "Diese Frage wurde schon beantwortet oder ist abgelaufen. Sag mir einfach noch einmal, was du möchtest.",
    },
}

Handler = Callable[[AsyncSession, Member, str, dict, str, date], Awaitable[Reply]]
Parser = Callable[[str, dict], str | None]

HANDLERS: dict[str, Handler] = {}
PARSERS: dict[str, Parser] = {}


_YES = frozenset("sim s yes y ja j oui o claro ok certo pode".split())
_NO = frozenset("nao n no nee non nein nope".split())


def yes_no(text: str, data: dict | None = None) -> str | None:
    """ "sim"/"não" in the five languages as ``yes``/``no`` (plain words only, accents stripped)."""
    from alfred.parsing import strip_accents

    word = strip_accents(text.strip(" .!?").lower())
    if word in _YES:
        return "yes"
    if word in _NO:
        return "no"
    return None


def register(type_: str, handler: Handler, parser: Parser | None = None) -> None:
    HANDLERS[type_] = handler
    if parser is not None:
        PARSERS[type_] = parser


async def discard(session: AsyncSession, member_id: uuid.UUID) -> None:
    await session.execute(
        delete(PendingAction).where(
            PendingAction.member_id == member_id, PendingAction.kind == KIND
        )
    )


async def ask(
    session: AsyncSession,
    member: Member,
    type_: str,
    data: dict[str, Any],
    text: str,
    options: list[tuple[str, str]],
) -> Reply:
    """Open the question (replacing any older one) and build the reply with its buttons or list."""
    await discard(session, member.id)
    row = PendingAction(
        id=uuid.uuid4(),
        member_id=member.id,
        kind=KIND,
        payload={"type": type_, "data": data},
        expires_at=datetime.now(UTC) + timedelta(minutes=TTL_MINUTES),
    )
    session.add(row)
    await session.flush()
    return Reply(text, [(f"fu:{row.id}:{choice}", label) for choice, label in options])


async def open_question(session: AsyncSession, member_id: uuid.UUID) -> PendingAction | None:
    row = await session.scalar(
        select(PendingAction).where(
            PendingAction.member_id == member_id, PendingAction.kind == KIND
        )
    )
    if row is None:
        return None
    if row.expires_at < datetime.now(UTC) or row.payload.get("type") not in HANDLERS:
        await session.delete(row)
        return None
    return row


async def _run(
    session: AsyncSession, member: Member, lang: str, row: PendingAction, choice: str, today: date
) -> Reply:
    payload = dict(row.payload)
    await session.delete(row)  # consumed: a second tap on the same button finds nothing
    await session.flush()
    return await HANDLERS[payload["type"]](
        session, member, lang, payload.get("data") or {}, choice, today
    )


async def on_button(
    session: AsyncSession, member: Member, lang: str, button_id: str, today: date
) -> Reply | None:
    """``fu:<action id>:<choice>``. A stale, foreign or forged id does nothing (returns None)."""
    _, _, rest = button_id.partition(":")
    raw_id, _, choice = rest.partition(":")
    try:
        action_id = uuid.UUID(raw_id)
    except ValueError:
        return None
    row = await session.scalar(
        select(PendingAction).where(
            PendingAction.id == action_id,
            PendingAction.member_id == member.id,
            PendingAction.kind == KIND,
        )
    )
    if row is None or row.expires_at < datetime.now(UTC) or row.payload.get("type") not in HANDLERS:
        if row is not None:
            await session.delete(row)
        return None
    return await _run(session, member, lang, row, choice, today)


async def on_text(
    session: AsyncSession, member: Member, lang: str, text: str, today: date
) -> Reply | None:
    """A typed answer to the open question; any other message closes the question."""
    row = await open_question(session, member.id)
    if row is None:
        return None
    data = row.payload.get("data") or {}
    parse = PARSERS.get(row.payload["type"]) or (yes_no if data.get("yes_no") else None)
    choice = parse(text, data) if parse else None
    if choice is None:
        await session.delete(row)
        return None
    return await _run(session, member, lang, row, choice, today)


async def confirmation_open(session: AsyncSession, member_id: uuid.UUID) -> bool:
    """True when the open question is a yes/no one (so a bare "sim" answers it)."""
    row = await open_question(session, member_id)
    return row is not None and bool((row.payload.get("data") or {}).get("yes_no"))
