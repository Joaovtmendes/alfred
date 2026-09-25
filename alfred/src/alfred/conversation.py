"""Conversation handler — routes inbound messages to the right response.

State machine:
  pending          → sends disclosure, advances to pending_response
  pending_response + sim/yes/…   → accepted, sends confirmation
  pending_response + nao/no/…    → rejected, sends rejection message
  pending_response + ?           → stays pending_response, sends reminder
  rejected         → silently ignored
  accepted         → commands or LLM reply with conversation history
"""
from __future__ import annotations
import unicodedata

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.llm import classify_query, extract_expense, generate_reply
from alfred.models import Expense, Member, Message
from alfred.whatsapp import send_text

logger = structlog.get_logger()

# ── Consent strings ──────────────────────────────────────────────────────────

DISCLOSURE_NL = (
    "*Alfred* — assistente pessoal via WhatsApp.\n\n"
    "Sou uma inteligência artificial, não uma pessoa. "
    "As tuas mensagens são processadas para te dar suporte.\n\n"
    "Escreve *sim* para continuar ou *não* para cancelar."
)

CONSENT_ACCEPTED_NL = (
    "Tudo pronto. Podes começar agora.\n\n"
    "• \"gastei €45 no Jumbo\" — registar despesa\n"
    "• \"recebi €2.800 de salário\" — registar receita\n"
    "• \"resumo\" — ver os gastos do mês\n"
    "• \"ajuda\" — ver todos os comandos"
)

CONSENT_REJECTED_NL = (
    "Entendido. Se quiseres retomar, é só enviar uma mensagem."
)

CONSENT_UNKNOWN_NL = (
    "Responde *sim* para continuar ou *não* para cancelar."
)

# ── Command keywords ─────────────────────────────────────────────────────────

_SUMMARY_WORDS = {"resumo", "overzicht", "summary", "samenvatting", "gastos"}
_SALDO_WORDS = {"saldo", "balance", "balanço", "balancete", "balanso"}
_HELP_WORDS = {"ajuda", "help", "hulp", "comandos", "commands"}


def _is_command(body: str, keywords: set[str]) -> bool:
    """True when body *starts with* a command keyword (ignores trailing punctuation/words)."""
    for kw in keywords:
        if body == kw or body.startswith(kw + " ") or body.startswith(kw + "?") or body.startswith(kw + "!"):
            return True
    return False

HELP_TEXT = (
    "*Alfred* — o que posso fazer por ti:\n\n"
    "*Registar despesas*\n"
    "• \"gastei €45 no Jumbo\"\n"
    "• \"Uber 12,50\"\n"
    "• \"paguei €180 de renda\"\n"
    "• \"esqueci de anotar o almoço de ontem, €39\"\n\n"
    "*Registar receitas*\n"
    "• \"recebi €2.800 de salário\"\n"
    "• \"recebi €500 do freela\"\n\n"
    "*Consultas*\n"
    "• \"resumo\" — gastos detalhados do mês\n"
    "• \"saldo\" — balanço receitas/despesas\n"
    "• \"quanto gastei em supermercado?\" — por categoria\n"
    "• \"gastos desta semana\" — por período\n"
    "• \"compara este mês com o mês passado\" — comparação\n"
    "• \"ajuda\" — esta mensagem\n\n"
    "_Para sair: \"stop\"_"
)

# ── History window ───────────────────────────────────────────────────────────

_HISTORY_LIMIT = 10  # messages (pairs) to include in LLM context


async def _load_history(member: Member, session: AsyncSession) -> list[dict]:
    """Return the last N messages for this member as Claude-format dicts."""
    result = await session.execute(
        select(Message)
        .where(Message.author_id == member.id)
        .order_by(Message.created_at.desc())
        .limit(_HISTORY_LIMIT * 2)
    )
    rows = result.scalars().all()
    rows = list(reversed(rows))  # chronological order

    history: list[dict] = []
    for msg in rows:
        role = "user" if msg.direction == "inbound" else "assistant"
        if msg.body:
            history.append({"role": role, "content": msg.body})
    return history


async def _save_outbound(
    member: Member,
    body: str,
    session: AsyncSession,
) -> None:
    """Persist an outbound message so it appears in future history."""
    outbound = Message(
        id=uuid.uuid4(),
        wa_message_id=f"out-{uuid.uuid4()}",
        household_id=member.household_id,
        author_id=member.id,
        direction="outbound",
        body=body,
        wa_timestamp=datetime.now(timezone.utc),
        processed=True,
    )
    session.add(outbound)


def _fmt_eur(amount: float) -> str:
    """Format a float as PT-style euro: €1.234,56"""
    return f"€{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _period_range(period: str) -> tuple[datetime, datetime | None, str]:
    """Return (start, end_exclusive, label).  end_exclusive=None means open (up to now)."""
    now = datetime.now(timezone.utc)
    if period == "last_month":
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_last = first_this - timedelta(seconds=1)
        start = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, first_this, prev_last.strftime("%B %Y").capitalize()
    if period == "current_week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, None, "esta semana"
    if period == "last_week":
        start_this = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        start = start_this - timedelta(days=7)
        return start, start_this, "semana passada"
    # default: current_month
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, None, now.strftime("%B %Y").capitalize()


async def _build_summary(
    member: Member,
    session: AsyncSession,
    *,
    period: str = "current_month",
    category: str | None = None,
) -> str:
    """Query expenses/income for a period (optionally filtered by category)."""
    start, end_excl, period_label = _period_range(period)

    filters = [
        Expense.member_id == member.id,
        Expense.expense_date >= start,
    ]
    if end_excl:
        filters.append(Expense.expense_date < end_excl)
    if category:
        filters.append(Expense.category == category)

    result = await session.execute(
        select(Expense).where(*filters).order_by(Expense.expense_date.desc())
    )
    records = result.scalars().all()

    if not records:
        scope = f"em *{category.capitalize()}*" if category else f"em {period_label}"
        return f"Sem registos {scope}."

    outflows = [e for e in records if e.transaction_type != "income"]
    inflows = [e for e in records if e.transaction_type == "income"]
    total_out = sum(e.amount for e in outflows)
    total_in = sum(e.amount for e in inflows)

    if category:
        # Category view: list individual transactions
        title = f"*{category.capitalize()} — {period_label}*"
        lines = [f"{title}\n"]
        for e in outflows[:10]:
            date_str = e.expense_date.strftime("%d/%m")
            name = e.merchant or e.description or category
            lines.append(f"• {date_str} {name}: {_fmt_eur(e.amount)}")
        lines.append(f"\n*Total: {_fmt_eur(total_out)}*")
        lines.append(f"_{len(outflows)} transação(ões)_")
    else:
        # Full summary: group by category
        by_cat: dict[str, float] = {}
        for e in outflows:
            cat = e.category or "overig"
            by_cat[cat] = by_cat.get(cat, 0) + e.amount

        lines = [f"*Gastos — {period_label}*\n"]
        for cat, amt in sorted(by_cat.items(), key=lambda x: -x[1]):
            lines.append(f"• {cat.capitalize()}: {_fmt_eur(amt)}")

        lines.append(f"\n*Total despesas: {_fmt_eur(total_out)}*")

        if inflows:
            balance = total_in - total_out
            sign = "+" if balance >= 0 else ""
            lines.append(f"Receitas: {_fmt_eur(total_in)}")
            lines.append(f"_Saldo: {sign}{_fmt_eur(balance)}_")

        lines.append(f"_{len(outflows)} transação(ões)_")

    return "\n".join(lines)


async def _build_saldo(member: Member, session: AsyncSession) -> str:
    """Show income/expense balance for the current month."""
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    result = await session.execute(
        select(Expense).where(
            Expense.member_id == member.id,
            Expense.expense_date >= start,
        )
    )
    records = result.scalars().all()

    if not records:
        return "Sem registos este mês."

    total_in = sum(e.amount for e in records if e.transaction_type == "income")
    total_out = sum(e.amount for e in records if e.transaction_type != "income")
    balance = total_in - total_out
    sign = "+" if balance >= 0 else ""

    month_label = now.strftime("%B %Y").capitalize()
    lines = [
        f"*Saldo — {month_label}*\n",
        f"• Receitas: {_fmt_eur(total_in)}",
        f"• Despesas: {_fmt_eur(total_out)}",
        f"\n*Saldo: {sign}{_fmt_eur(balance)}*",
    ]
    return "\n".join(lines)


async def _build_comparison(member: Member, session: AsyncSession) -> str:
    """Compare current month vs previous month (expenses only)."""
    now = datetime.now(timezone.utc)
    cur_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_end = cur_start
    prev_last = cur_start - timedelta(seconds=1)
    prev_start = prev_last.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def _total_out(start: datetime, end: datetime) -> tuple[float, int]:
        r = await session.execute(
            select(Expense).where(
                Expense.member_id == member.id,
                Expense.expense_date >= start,
                Expense.expense_date < end,
                Expense.transaction_type != "income",
            )
        )
        rows = r.scalars().all()
        return sum(e.amount for e in rows), len(rows)

    cur_total, cur_n = await _total_out(cur_start, now + timedelta(seconds=1))
    prev_total, prev_n = await _total_out(prev_start, prev_end)

    cur_label = now.strftime("%B").capitalize()
    prev_label = prev_last.strftime("%B").capitalize()

    diff = cur_total - prev_total
    if prev_total == 0:
        diff_str = "sem dados no mês anterior"
    elif diff == 0:
        diff_str = "_igual ao mês anterior_"
    elif diff > 0:
        pct = diff / prev_total * 100
        diff_str = f"_+{_fmt_eur(diff)} (+{pct:.0f}%) vs {prev_label}_"
    else:
        pct = abs(diff) / prev_total * 100
        diff_str = f"_{_fmt_eur(diff)} (-{pct:.0f}%) vs {prev_label}_"

    lines = [
        "*Comparação de despesas*\n",
        f"• {prev_label}: {_fmt_eur(prev_total)} ({prev_n} transações)",
        f"• {cur_label}: {_fmt_eur(cur_total)} ({cur_n} transações)",
        f"\n{diff_str}",
    ]
    return "\n".join(lines)


async def handle_inbound(
    member: Member,
    message: Message,
    session: AsyncSession,
) -> None:
    """Decide what to reply based on consent state and message content."""
    to = member.wa_phone
    body = unicodedata.normalize("NFC", (message.body or "").strip()).lower()

    # ── 1. First contact: send disclosure ───────────────────────────────────
    if member.consent_state == "pending":
        await send_text(to, DISCLOSURE_NL)
        member.consent_state = "pending_response"
        session.add(member)
        logger.info("conversation.disclosure_sent", wa_phone=to)
        return

    # ── 2. Awaiting consent response ────────────────────────────────────────
    if member.consent_state == "pending_response":
        if body in ("sim", "yes", "s", "y", "ok", "aceito", "aceitar", "ja"):
            member.consent_state = "accepted"
            member.disclosure_accepted_at = datetime.now(timezone.utc)
            member.disclosure_version = "1.0"
            session.add(member)
            await send_text(to, CONSENT_ACCEPTED_NL)
            await _save_outbound(member, CONSENT_ACCEPTED_NL, session)
            logger.info("conversation.consent_accepted", wa_phone=to)
        elif body in ("não", "nao", "no", "n", "stop", "nee"):
            member.consent_state = "rejected"
            session.add(member)
            await send_text(to, CONSENT_REJECTED_NL)
            logger.info("conversation.consent_rejected", wa_phone=to)
        else:
            await send_text(to, CONSENT_UNKNOWN_NL)
        return

    # ── 3. Rejected — honour their choice ───────────────────────────────────
    if member.consent_state == "rejected":
        logger.info("conversation.rejected_member_ignored", wa_phone=to)
        return

    # ── 4. Accepted — handle commands and LLM ───────────────────────────────
    if member.consent_state == "accepted":

        # 4a. Stop — re-enter rejected state
        if body in ("stop", "pare", "parar", "stoppen", "ophouden"):
            member.consent_state = "rejected"
            session.add(member)
            await send_text(to, CONSENT_REJECTED_NL)
            logger.info("conversation.stop_requested", wa_phone=to)
            return

        # 4b. Saldo command
        if _is_command(body, _SALDO_WORDS):
            saldo = await _build_saldo(member, session)
            await send_text(to, saldo)
            await _save_outbound(member, saldo, session)
            return

        # 4c. Help command
        if _is_command(body, _HELP_WORDS):
            await send_text(to, HELP_TEXT)
            await _save_outbound(member, HELP_TEXT, session)
            return

        # 4d. Summary command
        if _is_command(body, _SUMMARY_WORDS):
            summary = await _build_summary(member, session)
            await send_text(to, summary)
            await _save_outbound(member, summary, session)
            return

        # 4e. Try to extract an expense or income from the message
        expense_data = await extract_expense(message.body or "")
        if expense_data:
            txn_type = expense_data.get("type", "expense")
            days_ago = expense_data.get("days_ago", 0)
            expense_date = datetime.now(timezone.utc) - timedelta(days=days_ago)

            expense = Expense(
                id=uuid.uuid4(),
                member_id=member.id,
                household_id=member.household_id,
                transaction_type=txn_type,
                amount=expense_data["amount"],
                currency=expense_data["currency"],
                merchant=expense_data["merchant"],
                category=expense_data["category"],
                description=expense_data["description"],
                expense_date=expense_date,
            )
            session.add(expense)

            label = "Receita" if txn_type == "income" else "Despesa"
            name = expense_data["merchant"] or expense_data["category"] or expense_data["description"]
            amt_fmt = _fmt_eur(expense_data["amount"])
            reply = f"{label} registada — {amt_fmt} em *{name}*"
            if days_ago > 0:
                reply += f" _(referente a {days_ago}d atrás)_"

            await send_text(to, reply)
            await _save_outbound(member, reply, session)
            logger.info(
                "conversation.expense_recorded",
                wa_phone=to,
                type=txn_type,
                amount=expense_data["amount"],
                category=expense_data["category"],
            )
            return

        # 4f. Try to classify as a financial query
        query = await classify_query(message.body or "")
        if query:
            qtype = query.get("query_type")
            period = query.get("period") or "current_month"
            category = query.get("category")

            if qtype == "balance":
                reply = await _build_saldo(member, session)
            elif qtype == "category" and category:
                reply = await _build_summary(member, session, period=period, category=category)
            elif qtype == "period":
                reply = await _build_summary(member, session, period=period)
            elif qtype == "comparison":
                reply = await _build_comparison(member, session)
            else:
                reply = None

            if reply:
                await send_text(to, reply)
                await _save_outbound(member, reply, session)
                logger.info("conversation.query_replied", wa_phone=to, query_type=qtype)
                return

        # 4g. General LLM reply with conversation history
        history = await _load_history(member, session)
        reply = await generate_reply(member, message, history=history)  # noqa: F821
        await send_text(to, reply)
        await _save_outbound(member, reply, session)
        logger.info("conversation.reply_sent", wa_phone=to)
        return
