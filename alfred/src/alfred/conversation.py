"""Conversation handler — routes inbound messages to the right response."""
from __future__ import annotations

import structlog

from alfred.models import Member, Message
from alfred.whatsapp import send_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# EU AI Act Art. 50 — disclosure text (NL primary, EN fallback)
DISCLOSURE_NL = (
    "Olá! Sou o Alfred, um assistente de IA que te ajuda com tarefas do dia a dia. "
    "Sou uma inteligência artificial, não uma pessoa. "
    "As tuas mensagens são processadas para te ajudar.\n\n"
    "Escreve *sim* para aceitar e continuar, ou *não* para parar."
)

CONSENT_ACCEPTED_NL = (
    "Obrigado! Podes começar agora. Em que te posso ajudar? 😊"
)

CONSENT_REJECTED_NL = (
    "Sem problema. Se mudares de ideias, é só enviar uma mensagem."
)

CONSENT_UNKNOWN_NL = (
    "Escreve *sim* para aceitar ou *não* para parar."
)


async def handle_inbound(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> None:
    """Decide what to reply based on consent state and message content."""
    to = member.wa_phone
    body = (message.body or "").strip().lower()

    # ── 1. First contact: send disclosure and wait for consent ──────────────
    if member.consent_state == "pending":
        await send_text(to, DISCLOSURE_NL)  # send first — only advance state on success
        member.consent_state = "pending_response"
        session.add(member)
        logger.info("conversation.disclosure_sent", wa_phone=to)
        return

    # ── 2. Consent response ─────────────────────────────────────────────────
    if member.consent_state == "pending_response":
        if body in ("sim", "yes", "s", "y", "ok", "aceito", "aceitar", "ja"):
            from datetime import datetime, timezone
            member.consent_state = "accepted"
            member.disclosure_accepted_at = datetime.now(timezone.utc)
            member.disclosure_version = "1.0"
            session.add(member)
            await send_text(to, CONSENT_ACCEPTED_NL)
            logger.info("conversation.consent_accepted", wa_phone=to)
        elif body in ("não", "nao", "no", "n", "stop", "nee"):
            member.consent_state = "rejected"
            session.add(member)
            await send_text(to, CONSENT_REJECTED_NL)
            logger.info("conversation.consent_rejected", wa_phone=to)
        else:
            await send_text(to, CONSENT_UNKNOWN_NL)
        return

    # ── 3. Consent rejected — do not process ───────────────────────────────
    if member.consent_state == "rejected":
        # Honour their choice — no reply
        logger.info("conversation.rejected_member_ignored", wa_phone=to)
        return

    # ── 4. Accepted — route to LLM ─────────────────────────────────────────
    if member.consent_state == "accepted":
        from alfred.llm import generate_reply
        reply = await generate_reply(member, message)
        await send_text(to, reply)
        logger.info("conversation.reply_sent", wa_phone=to)
        return
