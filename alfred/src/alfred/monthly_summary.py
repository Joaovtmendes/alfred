"""V2-04 — automatic monthly summary (sent on the 1st for the month that just ended).

Pure rules and SQL, no LLM: totals, top categories, change vs. the month before, budget
outcomes (V2-01), fixed bills (V2-02), workouts and habits in one line, a pointer to the
dashboard. Never persisted: it is computed when it is sent or asked for.

Delivery (see ``scripts/daily_cron.py``): inside the 24 h window as a plain message, outside it
only through the approved template ``alfred_monthly_summary`` (flag off until Meta approves it).
Opt-out is a ``ScheduledJob(job_type="monthly_summary", active=False)`` row; the same row's
``last_sent_at`` makes the send idempotent per month.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alfred.audit import audit
from alfred.budgets import _month_window, month_spent, percent
from alfred.clock import local_tz, month_name, month_start, prev_month_start, to_local, today_local
from alfred.labels import category_label
from alfred.models import (
    SETTLED,
    Budget,
    Expense,
    HabitLog,
    Member,
    Message,
    RecurringItem,
    ScheduledJob,
    WorkoutSession,
)

JOB_TYPE = "monthly_summary"
SEND_FROM_HOUR = 9
SEND_UNTIL_HOUR = 20
SEND_UNTIL_DAY = 3  # a missed 1st (e.g. waiting for the 24 h window) is still sent on days 2–3
WINDOW_HOURS = 24
TOP_CATEGORIES = 3

_Q = Decimal("0.01")


def _dec(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(_Q, ROUND_HALF_UP)


async def _totals(
    session: AsyncSession, member_id: uuid.UUID, first: date
) -> tuple[Decimal, Decimal, list[tuple[str, Decimal]]]:
    start, end = _month_window(first, local_tz())
    rows = (
        await session.execute(
            select(Expense.transaction_type, Expense.category, func.sum(Expense.amount))
            .where(
                Expense.member_id == member_id,
                Expense.status.in_(SETTLED),
                Expense.expense_date >= start,
                Expense.expense_date < end,
            )
            .group_by(Expense.transaction_type, Expense.category)
        )
    ).all()
    income = sum((_dec(r[2]) for r in rows if r[0] == "income"), Decimal("0"))
    expense = sum((_dec(r[2]) for r in rows if r[0] != "income"), Decimal("0"))
    by_cat = sorted(
        ((r[1] or "overig", _dec(r[2])) for r in rows if r[0] != "income"),
        key=lambda x: x[1],
        reverse=True,
    )
    return income, expense, by_cat


async def build_summary(
    session: AsyncSession, member: Member, lang: str, first: date, *, one_line: bool = False
) -> str | None:
    """The summary of the month starting ``first``; None when the member has no history at all.

    ``one_line`` joins the parts with " · " (template variables cannot contain newlines).
    """
    from alfred.conversation import _fmt_eur, _t

    income, expense, by_cat = await _totals(session, member.id, first)
    month = month_name(first, lang)
    if income == 0 and expense == 0:
        ever = await session.scalar(
            select(func.count()).select_from(Expense).where(Expense.member_id == member.id)
        )
        if not ever:
            return None
        return _t("msum_empty", lang, month=month)

    parts: list[str] = [
        _t(
            "msum_totals",
            lang,
            income=_fmt_eur(float(income)),
            expense=_fmt_eur(float(expense)),
            balance=_fmt_eur(float(income - expense)),
        )
    ]
    if by_cat:
        top = ", ".join(
            f"{category_label(c, lang)} {_fmt_eur(float(a))}" for c, a in by_cat[:TOP_CATEGORIES]
        )
        parts.append(_t("msum_top", lang, cats=top))
    prev_first = prev_month_start(first)
    _, prev_exp, _ = await _totals(session, member.id, prev_first)
    if prev_exp > 0:
        change = int(((expense - prev_exp) * 100 / prev_exp).to_integral_value(ROUND_HALF_UP))
        parts.append(
            _t("msum_vs_prev", lang, prev=month_name(prev_first, lang), change=f"{change:+d}")
        )
    from alfred.ledger_status import pending_summary

    pend = await pending_summary(session, member.id)
    if pend.overdue_count:
        parts.append(
            _t(
                "pending_overdue",
                lang,
                n=pend.overdue_count,
                total=_fmt_eur(float(pend.overdue_total)),
            )
        )
    budgets = (
        (await session.execute(select(Budget).where(Budget.member_id == member.id))).scalars().all()
    )
    if budgets:
        ok, over = 0, []
        for b in budgets:
            spent = await month_spent(session, member.id, b.category, first)
            if percent(spent, _dec(b.monthly_limit)) > 100:
                over.append(category_label(b.category, lang))
            else:
                ok += 1
        parts.append(
            _t("msum_budgets_over", lang, ok=ok, over=", ".join(over))
            if over
            else _t("msum_budgets_ok", lang, ok=ok)
        )
    bills = (
        (
            await session.execute(
                select(RecurringItem).where(
                    RecurringItem.member_id == member.id, RecurringItem.active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    if bills:
        from alfred.recurring import monthly_equivalent

        total = sum((monthly_equivalent(b.amount, b.frequency) for b in bills), Decimal("0"))
        parts.append(_t("msum_bills", lang, n=len(bills), total=_fmt_eur(float(total))))
    last_day = month_start(first + timedelta(days=32))
    workouts = await session.scalar(
        select(func.count())
        .select_from(WorkoutSession)
        .where(
            WorkoutSession.member_id == member.id,
            WorkoutSession.workout_date >= first,
            WorkoutSession.workout_date < last_day,
        )
    )
    habits = await session.scalar(
        select(func.count())
        .select_from(HabitLog)
        .where(
            HabitLog.member_id == member.id,
            HabitLog.log_date >= first,
            HabitLog.log_date < last_day,
        )
    )
    if workouts or habits:
        parts.append(_t("msum_activity", lang, workouts=workouts or 0, habits=habits or 0))
    parts.append(_t("msum_footer", lang))

    header = _t("msum_header", lang, month=month)
    if one_line:
        return header + " " + " · ".join(p.rstrip(".") for p in parts) + "."
    return "\n".join([header, *parts])


# ── commands ─────────────────────────────────────────────────────────────────

_OFF_RE = re.compile(
    r"^(?:sem\s+resumo\s+mensal|(?:desativ\w+|desligar|cancelar|parar)\s+(?:o\s+)?resumo\s+mensal|"
    r"(?:no|stop)\s+monthly\s+summary|(?:turn\s+off|disable)\s+(?:the\s+)?monthly\s+summary|"
    r"geen\s+maandoverzicht|stop\s+(?:het\s+)?maandoverzicht|"
    r"pas\s+de\s+bilan\s+mensuel|keine\s+monatsuebersicht|monatsuebersicht\s+aus)\s*[.!]*$"
)
_ON_RE = re.compile(
    r"^(?:(?:ativ\w+|ligar|quero)\s+(?:o\s+)?resumo\s+mensal|(?:turn\s+on|enable|start)\s+(?:the\s+)?monthly\s+summary|"
    r"activeer\s+(?:het\s+)?maandoverzicht|active\s+le\s+bilan\s+mensuel|monatsuebersicht\s+an)\s*[.!]*$"
)
_NOW_WORDS = {
    "resumo mensal", "meu resumo mensal", "monthly summary", "my monthly summary",
    "maandoverzicht", "mijn maandoverzicht", "bilan mensuel", "mon bilan mensuel",
    "monatsuebersicht", "meine monatsuebersicht",
}  # fmt: skip


async def set_opt_in(session: AsyncSession, member_id: uuid.UUID, on: bool) -> None:
    job = await session.scalar(
        select(ScheduledJob).where(
            ScheduledJob.member_id == member_id, ScheduledJob.job_type == JOB_TYPE
        )
    )
    if job is None:
        session.add(
            ScheduledJob(
                id=uuid.uuid4(),
                member_id=member_id,
                job_type=JOB_TYPE,
                time_of_day="09:00",
                days_mask=0,  # the generic reminder loop never fires this row
                active=on,
            )
        )
    else:
        job.active = on
        session.add(job)
    await session.flush()


async def handle_monthly_summary_command(
    body_plain: str, member: Member, lang: str, session: AsyncSession
) -> str | None:
    from alfred.conversation import _t

    plain = re.sub(r"\s+", " ", body_plain).strip(" .!?")
    if _OFF_RE.match(plain):
        await set_opt_in(session, member.id, False)
        audit(session, "monthly_summary_off", member.id)
        return _t("msum_off", lang)
    if _ON_RE.match(plain):
        await set_opt_in(session, member.id, True)
        audit(session, "monthly_summary_on", member.id)
        return _t("msum_on", lang)
    if plain in _NOW_WORDS:
        text = await build_summary(session, member, lang, prev_month_start(today_local()))
        return text or _t("msum_none_yet", lang)
    return None


# ── cron side ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DueSummary:
    member_id: uuid.UUID
    wa_phone: str
    lang: str
    text: str
    one_line: str
    in_window: bool


async def pending_summaries(session: AsyncSession, now: datetime) -> list[DueSummary]:
    """Members owed last month's summary: day 1–3, 09:00–20:00 local, once per month, opted in."""
    if not (SEND_FROM_HOUR <= now.hour < SEND_UNTIL_HOUR) or now.day > SEND_UNTIL_DAY:
        return []
    this_month = month_start(now.date())
    first = prev_month_start(now.date())
    members = (
        await session.execute(
            select(Member.id, Member.wa_phone, Member.language).where(
                Member.consent_state == "accepted"
            )
        )
    ).all()
    jobs = {
        j.member_id: j
        for j in (
            await session.execute(select(ScheduledJob).where(ScheduledJob.job_type == JOB_TYPE))
        )
        .scalars()
        .all()
    }
    cutoff = now - timedelta(hours=WINDOW_HOURS)
    out: list[DueSummary] = []
    for mid, phone, language in members:
        job = jobs.get(mid)
        if job is not None and not job.active:
            continue
        if job is not None and job.last_sent_at is not None:
            if month_start(to_local(job.last_sent_at).date()) >= this_month:
                continue
        lang = language or "en"
        member = await session.get(Member, mid)
        text = await build_summary(session, member, lang, first)
        if text is None:
            continue
        one_line = await build_summary(session, member, lang, first, one_line=True) or text
        last_in = await session.scalar(
            select(func.max(Message.created_at)).where(
                Message.author_id == mid, Message.direction == "inbound"
            )
        )
        out.append(
            DueSummary(
                member_id=mid,
                wa_phone=phone,
                lang=lang,
                text=text,
                one_line=one_line,
                in_window=bool(last_in and last_in.astimezone(now.tzinfo) >= cutoff),
            )
        )
    return out


async def mark_sent(session: AsyncSession, member_id: uuid.UUID, when: datetime) -> None:
    job = await session.scalar(
        select(ScheduledJob).where(
            ScheduledJob.member_id == member_id, ScheduledJob.job_type == JOB_TYPE
        )
    )
    if job is None:
        job = ScheduledJob(
            id=uuid.uuid4(),
            member_id=member_id,
            job_type=JOB_TYPE,
            time_of_day="09:00",
            days_mask=0,
        )
    job.last_sent_at = when
    session.add(job)


_L = ("pt", "nl", "en", "fr", "de")

STRINGS: dict[str, dict[str, str | tuple[str, ...]]] = {
    "msum_header": {"pt": "Resumo de {month}:", "nl": "Overzicht van {month}:", "en": "Your {month} summary:", "fr": "Bilan de {month} :", "de": "Dein Überblick für {month}:"},
    "msum_totals": {
        "pt": "Receitas {income} · Despesas {expense} · Saldo {balance}.",
        "nl": "Inkomsten {income} · Uitgaven {expense} · Saldo {balance}.",
        "en": "Income {income} · Spending {expense} · Balance {balance}.",
        "fr": "Revenus {income} · Dépenses {expense} · Solde {balance}.",
        "de": "Einnahmen {income} · Ausgaben {expense} · Saldo {balance}.",
    },
    "msum_top": {
        "pt": "Onde mais foi: {cats}.",
        "nl": "Meeste uitgaven: {cats}.",
        "en": "Biggest categories: {cats}.",
        "fr": "Plus gros postes : {cats}.",
        "de": "Größte Posten: {cats}.",
    },
    "msum_vs_prev": {
        "pt": "Despesas {change}% em relação a {prev}.",
        "nl": "Uitgaven {change}% t.o.v. {prev}.",
        "en": "Spending {change}% vs {prev}.",
        "fr": "Dépenses {change} % par rapport à {prev}.",
        "de": "Ausgaben {change} % gegenüber {prev}.",
    },
    "msum_budgets_ok": {
        "pt": "Orçamentos: todos dentro do limite ({ok}).",
        "nl": "Budgetten: allemaal binnen de limiet ({ok}).",
        "en": "Budgets: all within the limit ({ok}).",
        "fr": "Budgets : tous dans la limite ({ok}).",
        "de": "Budgets: alle im Limit ({ok}).",
    },
    "msum_budgets_over": {
        "pt": "Orçamentos: dentro do limite {ok}, estourado: {over}.",
        "nl": "Budgetten: binnen de limiet {ok}, overschreden: {over}.",
        "en": "Budgets: within the limit {ok}, over: {over}.",
        "fr": "Budgets : dans la limite {ok}, dépassé : {over}.",
        "de": "Budgets: im Limit {ok}, überschritten: {over}.",
    },
    "msum_bills": {
        "pt": "Contas fixas: {n}, {total} por mês.",
        "nl": "Vaste lasten: {n}, {total} per maand.",
        "en": "Recurring bills: {n}, {total} a month.",
        "fr": "Charges fixes : {n}, {total} par mois.",
        "de": "Fixkosten: {n}, {total} pro Monat.",
    },
    "msum_activity": {
        "pt": "Treinos: {workouts} · Hábitos registrados: {habits}.",
        "nl": "Trainingen: {workouts} · Gewoontes gelogd: {habits}.",
        "en": "Workouts: {workouts} · Habit check-ins: {habits}.",
        "fr": "Entraînements : {workouts} · Habitudes enregistrées : {habits}.",
        "de": "Trainings: {workouts} · Gewohnheiten erfasst: {habits}.",
    },
    "msum_footer": {
        "pt": "Para ver tudo em gráficos, diga “meu dashboard”.",
        "nl": "Zie alles in grafieken: zeg “mijn dashboard”.",
        "en": "To see it all in charts, say “my dashboard”.",
        "fr": "Pour tout voir en graphiques, dis « mon tableau de bord ».",
        "de": "Alles als Diagramme: sag „mein Dashboard“.",
    },
    "msum_empty": {
        "pt": "Resumo de {month}: não vi lançamentos desse mês. Que tal retomar? É só me mandar “mercado 25”.",
        "nl": "Overzicht van {month}: ik zag geen transacties. Zin om weer te beginnen? Stuur me gewoon “boodschappen 25”.",
        "en": "Your {month} summary: I didn't see any entries. Want to pick it up again? Just send “groceries 25”.",
        "fr": "Bilan de {month} : je n'ai vu aucune opération. On reprend ? Envoie-moi simplement « courses 25 ».",
        "de": "Überblick für {month}: Ich habe keine Einträge gesehen. Lust, wieder anzufangen? Schick mir einfach „Lebensmittel 25“.",
    },
    "msum_none_yet": {
        "pt": "Ainda não tenho lançamentos seus para resumir. Mande, por exemplo, “mercado 25”.",
        "nl": "Ik heb nog geen transacties om samen te vatten. Stuur bijvoorbeeld “boodschappen 25”.",
        "en": "I don't have any entries to summarise yet. Send, for example, “groceries 25”.",
        "fr": "Je n'ai encore aucune opération à résumer. Envoie par exemple « courses 25 ».",
        "de": "Ich habe noch keine Einträge zum Zusammenfassen. Schick zum Beispiel „Lebensmittel 25“.",
    },
    "msum_off": {
        "pt": "Combinado, não envio mais o resumo mensal. Para voltar, diga “ativar resumo mensal”.",
        "nl": "Afgesproken, geen maandoverzicht meer. Zeg “activeer maandoverzicht” om het weer aan te zetten.",
        "en": "Done, no more monthly summaries. Say “turn on monthly summary” to bring it back.",
        "fr": "C'est noté, plus de bilan mensuel. Dis « active le bilan mensuel » pour le remettre.",
        "de": "Erledigt, keine Monatsübersicht mehr. Sag „Monatsübersicht an“, um sie wieder zu aktivieren.",
    },
    "msum_on": {
        "pt": "Pronto, todo dia 1 eu mando o resumo do mês anterior.",
        "nl": "Klaar, op de 1e stuur ik het overzicht van de vorige maand.",
        "en": "Done, on the 1st of each month I'll send last month's summary.",
        "fr": "C'est fait, le 1er de chaque mois je t'envoie le bilan du mois passé.",
        "de": "Erledigt, am 1. jedes Monats schicke ich dir die Übersicht des Vormonats.",
    },
}  # fmt: skip
