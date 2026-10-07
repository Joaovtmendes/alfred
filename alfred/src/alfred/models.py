"""SQLAlchemy ORM models.

M1 — household / member / message (baseline)
M2 — expense table + preferred_name / language on member

Design decisions:
- All PKs are UUID generated server-side (no auto-increment)
- wa_message_id has a UNIQUE constraint → idempotency key for deduplication
- member.consent_state: pending | pending_language | pending_response | accepted | rejected
- message.direction: inbound | outbound
- All timestamps in UTC (timestamptz)
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from alfred.crypto import EncryptedText
from alfred.db import Base


class Household(Base):
    __tablename__ = "household"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list[Member]] = relationship(back_populates="household")
    messages: Mapped[list[Message]] = relationship(back_populates="household")
    expenses: Mapped[list[Expense]] = relationship(back_populates="household")


class Member(Base):
    __tablename__ = "member"
    __table_args__ = (
        UniqueConstraint("wa_phone", name="uq_member_wa_phone"),
        Index("ix_member_household_id", "household_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    wa_phone: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))

    # M2 — user profile
    preferred_name: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="pt")

    # AVG consent + EU AI Act Art. 50 disclosure
    consent_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | pending_language | pending_response | accepted | rejected
    disclosure_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disclosure_version: Mapped[str | None] = mapped_column(String(20))

    # M11 — Dashboard
    dashboard_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, unique=True, default=None
    )
    dashboard_token_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # V2-03 — panel v2: per-member flag, and a separate single-use token for the data export
    dashboard_v2: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    export_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, unique=True, default=None
    )
    export_token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # V2-10 — categories that are "da casa" by default for this member (canonical identifiers)
    home_categories: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)

    # V2-12 — accounting tab. ``acct_mode``: personal (default) | business (ZZP: adds the business
    # cards). ``business_categories``: categories that are business by default. ``tax_reserve_pct``
    # and ``savings_amount`` are numbers the member gave; nothing is assumed when they are unset.
    acct_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="personal", server_default="personal"
    )
    business_categories: Mapped[list[str] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    tax_reserve_pct: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    savings_amount: Mapped[float | None] = mapped_column(
        Numeric(12, 2, asdecimal=False), nullable=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped[Household] = relationship(back_populates="members")
    messages: Mapped[list[Message]] = relationship(back_populates="author")
    expenses: Mapped[list[Expense]] = relationship(back_populates="member")


class Message(Base):
    """Inbound and outbound WhatsApp messages.

    wa_message_id is the Meta-assigned ID — unique constraint ensures
    idempotency: re-delivered webhooks are silently ignored.
    """

    __tablename__ = "message"
    __table_args__ = (
        UniqueConstraint("wa_message_id", name="uq_message_wa_message_id"),
        # conversation history: latest messages of one author
        Index("ix_message_author_created", "author_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    wa_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("member.id"))
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # inbound | outbound
    body: Mapped[str | None] = mapped_column(Text)
    wa_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A handler run failed AFTER the reply went out: the retry redoes the work but must not
    # send it again (see alfred.delivery).
    reply_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    # V2-18 — what the bot sent and what Meta says happened to it (outbound rows only).
    # kind: reply | reminder | summary | alert | template; delivery_status: sent | delivered |
    # read | failed (from the Meta status webhook).
    kind: Mapped[str | None] = mapped_column(String(12))
    template_name: Mapped[str | None] = mapped_column(String(64))
    delivery_status: Mapped[str | None] = mapped_column(String(10))
    delivery_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped[Household] = relationship(back_populates="messages")
    author: Mapped[Member | None] = relationship(back_populates="messages")


SETTLED = ("paid", "received")
PENDING = ("to_pay", "to_receive")


def _default_status(context) -> str:
    """Settled by default: an income is "received", an expense is "paid"."""
    params = context.get_current_parameters()
    return "received" if params.get("transaction_type") == "income" else "paid"


class Expense(Base):
    """A financial transaction recorded by a member via natural language."""

    __tablename__ = "expense"
    __table_args__ = (
        # every summary/dashboard/saldo query filters by member and a date range
        Index("ix_expense_member_date", "member_id", "expense_date"),
        # V2-05: a statement line is imported once per member
        Index(
            "uq_expense_member_external",
            "member_id",
            "external_id",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )

    # expense | income
    transaction_type: Mapped[str] = mapped_column(String(10), nullable=False, default="expense")

    # NUMERIC(12,2) in the database (exact cents, exact SUM); asdecimal=False keeps
    # the Python side a float so display/JSON code is unchanged.
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="EUR")
    merchant: Mapped[str | None] = mapped_column(String(255))
    category: Mapped[str | None] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)

    # The date the expense actually happened (defaults to now if not extracted)
    expense_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # M14 — Viagem
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip.id"), nullable=True, default=None, index=True
    )
    # V2-16 — paid | to_pay | received | to_receive. Totals, balance and reports count only the
    # settled states (``SETTLED``); the pending ones are shown apart as "forecast".
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, default=_default_status, server_default="paid"
    )
    # V2-10 — "da casa": counted in the couple's shared balance while the member has an active
    # ``PartnerLink``. Personal (False) by default; nothing is shared unless the member says so.
    shared: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # V2-05 — where the entry came from: manual (typed in the chat) | csv (a statement file).
    # ``external_id`` is a stable hash of the statement line; one per member, so the same line
    # can never be imported twice. ``import_batch_id`` lets "desfazer importação" find them.
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual", server_default="manual"
    )
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # V2-12 — personal | business; ``btw_rate`` is the VAT percentage inside the amount (0, 9 or 21)
    # when the member gave it; ``deductible`` only matters for business spending.
    scope: Mapped[str] = mapped_column(
        String(10), nullable=False, default="personal", server_default="personal"
    )
    btw_rate: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    deductible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("import_batch.id", ondelete="SET NULL"), nullable=True
    )

    member: Mapped[Member] = relationship(back_populates="expenses")
    household: Mapped[Household] = relationship(back_populates="expenses")


class ScheduledJob(Base):
    """Proactive notification schedule for a member.

    M5 — each row represents one recurring reminder or weekly summary.
    scripts/daily_cron.py (every 15 min) reads this table and sends due templates.

    job_type:
        weekly_summary        — sent every Monday morning
        medication_reminder   — sent daily at time_of_day
        goal_checkin          — sent daily at time_of_day
        workout_reminder      — sent daily at time_of_day

    days_mask: bitmask Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64
               127 = every day, 1 = Monday only
    """

    __tablename__ = "scheduled_job"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # weekly_summary | medication_reminder | goal_checkin | workout_reminder
    time_of_day: Mapped[str] = mapped_column(
        String(5), nullable=False, default="08:00"
    )  # "HH:MM" local time in settings.timezone (Europe/Amsterdam)
    days_mask: Mapped[int] = mapped_column(nullable=False, default=127)  # bitmask; 127 = every day
    payload: Mapped[dict | None] = mapped_column(
        JSONB
    )  # extra params e.g. {"text": "toma medicamento"}
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


class MerchantCategoryOverride(Base):
    """M12 — User-taught category corrections.

    When a member corrects Alfred's category for a merchant
    (e.g. "Jumbo is supermarkt, not restaurant"), Alfred stores the override
    here and applies it automatically the next time that merchant appears.

    merchant is normalised to lowercase for case-insensitive matching.
    Unique constraint: one override per (member_id, merchant) pair.
    """

    __tablename__ = "merchant_category_overrides"
    __table_args__ = (UniqueConstraint("member_id", "merchant", name="uq_mco_member_merchant"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    merchant: Mapped[str] = mapped_column(String(255), nullable=False)  # normalised lowercase
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    member: Mapped[Member] = relationship()


# ── M7 — Treino ─────────────────────────────────────────────────────────────


class WorkoutSession(Base):
    """Registo de uma sessão de treino/exercício."""

    __tablename__ = "workout_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    activity_type: Mapped[str] = mapped_column(
        String(100), nullable=False
    )  # e.g. "running", "strength", "cycling"
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    workout_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


# ── M8 — Saúde ──────────────────────────────────────────────────────────────


class HealthLog(Base):
    """Registo de saúde: medicação, humor, sono, água."""

    __tablename__ = "health_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    log_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # medication | mood | sleep | water
    value: Mapped[str] = mapped_column(
        EncryptedText, nullable=False
    )  # "omeprazol", "7", "6.5" — encrypted at rest when DATA_ENCRYPTION_KEY is set (S1-06)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "/10", "hours", "mg", "L"
    notes: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


# ── M9 — Metas & Hábitos ────────────────────────────────────────────────────


class Goal(Base):
    """Meta pessoal do utilizador."""

    __tablename__ = "goal"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_value: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "500", "3x"
    target_unit: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "EUR/month", "per week"
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()
    habit_logs: Mapped[list[HabitLog]] = relationship(back_populates="goal")


class HabitLog(Base):
    """Registo diário de um hábito ligado a uma meta."""

    __tablename__ = "habit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goal.id"), nullable=True, index=True
    )
    activity: Mapped[str] = mapped_column(String(255), nullable=False)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()
    goal: Mapped[Goal | None] = relationship(back_populates="habit_logs")


# ── M10 — Produtividade ──────────────────────────────────────────────────────


class Note(Base):
    """Nota rápida do utilizador."""

    __tablename__ = "note"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


class Task(Base):
    """Tarefa do utilizador, com prazo e estado de conclusão."""

    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


# ── M14 — Viagem ─────────────────────────────────────────────────────────────


class Trip(Base):
    """Registo de uma viagem do utilizador."""

    __tablename__ = "trip"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    destination: Mapped[str] = mapped_column(String(200), nullable=False)
    started_at: Mapped[date] = mapped_column(Date, nullable=False)
    ended_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Optional budget stated when the trip starts ("criar viagem Portugal €500")
    budget: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped[Member] = relationship()


# ── Sprint 4b — Audit trail ──────────────────────────────────────────────────


class AuditLog(Base):
    """Security/privacy event (link issued, data exported/deleted...). No message content.

    ``member_id`` is set to NULL when the member is erased, so the trail survives without
    identifying anyone.
    """

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="SET NULL"), index=True
    )
    event: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── V1-21 — LLM usage (tokens per call, for the cost-per-user metric) ────────


class LlmUsage(Base):
    """Tokens of one LLM call, attributed to the member whose message caused it.

    No message text is stored. Erased with the member like every other per-member table.
    """

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_member_created", "member_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── V2-01 — Orçamentos por categoria ────────────────────────────────────────


class Budget(Base):
    """Monthly spending cap for one category of one member (V2-01).

    ``last_alert_month`` / ``last_alert_level`` (0 | 80 | 100) make each alert fire once per
    level per month: a new month starts again from level 0 without any cleanup job.
    """

    __tablename__ = "budget"
    __table_args__ = (UniqueConstraint("member_id", "category", name="uq_budget_member_category"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    monthly_limit: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    last_alert_month: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_alert_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ── V2-02 — Contas fixas ────────────────────────────────────────────────────


class RecurringItem(Base):
    """A fixed bill, subscription or instalment plan (V2-02).

    ``next_due_date`` moves forward each time the member says they paid it;
    ``last_reminded_for`` holds the due date a reminder was already sent for (idempotent cron).
    ``contract_end_date`` / ``provider`` are reserved for V2-27 (home contracts).
    """

    __tablename__ = "recurring_item"
    __table_args__ = (Index("ix_recurring_member_active", "member_id", "active"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    category: Mapped[str | None] = mapped_column(String(50))
    # fixed | subscription | installment
    kind: Mapped[str] = mapped_column(String(14), nullable=False, default="fixed")
    # monthly | yearly | weekly
    frequency: Mapped[str] = mapped_column(String(10), nullable=False, default="monthly")
    due_day: Mapped[int | None] = mapped_column(Integer)
    next_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    installments_total: Mapped[int | None] = mapped_column(Integer)
    installments_paid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_date: Mapped[date | None] = mapped_column(Date)
    remind_days_before: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_reminded_for: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)  # V2-27
    provider: Mapped[str | None] = mapped_column(String(120))  # V2-27
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── V2-06 — Agenda ──────────────────────────────────────────────────────────


class Appointment(Base):
    """A one-off appointment with a reminder (V2-06). Recurring things are ScheduledJob."""

    __tablename__ = "appointment"
    __table_args__ = (Index("ix_appointment_member_starts", "member_id", "starts_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    remind_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    # active | done | cancelled
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SavedView(Base):
    """A saved analysis (V2-14): a validated spec, never SQL or free text from the model.

    ``name`` NULL marks the member's *latest unnamed analysis* (a draft kept so "save this
    view as ..." has something to save); at most one draft per member is kept.
    """

    __tablename__ = "saved_view"
    __table_args__ = (Index("ix_saved_view_member", "member_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(40))
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Iou(Base):
    """Money a person owes the member, or the member owes a person (V2-15).

    ``settled_amount`` grows with partial payments; ``settled_at`` is set when nothing is
    left. Rows are kept after settling (history), erased/exported with the member.
    """

    __tablename__ = "iou"
    __table_args__ = (Index("ix_iou_member_open", "member_id", "settled_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )
    person: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    settled_amount: Mapped[float] = mapped_column(
        Numeric(12, 2, asdecimal=False), nullable=False, default=0, server_default="0"
    )
    # owed_to_me | i_owe
    direction: Mapped[str] = mapped_column(String(12), nullable=False)
    note: Mapped[str | None] = mapped_column(String(120))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerLink(Base):
    """V2-10 — two members who share the household finances (a couple).

    ``status``: ``invited`` (a code was issued, nobody used it yet) → ``pending`` (the partner
    typed the code and has to accept) → ``active`` → ``ended``. ``inviter_pct`` is the share of
    the shared expenses the inviter pays (the partner pays the rest); 50 by default.
    Deleting either member deletes the link (and its settlements).
    """

    __tablename__ = "partner_link"
    __table_args__ = (
        UniqueConstraint("code", name="uq_partner_link_code"),
        Index("ix_partner_link_inviter", "inviter_id"),
        Index("ix_partner_link_partner", "partner_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inviter_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=True
    )
    code: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="invited")
    inviter_pct: Mapped[int] = mapped_column(nullable=False, default=50, server_default="50")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerSettlement(Base):
    """V2-10 — money one partner handed to the other to settle the shared balance."""

    __tablename__ = "partner_settlement"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("partner_link.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # the member who paid (the debtor at that moment)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImportBatch(Base):
    """V2-05 — one statement file a member sent. The raw file is never stored.

    ``draft`` holds the normalised lines (JSON) until the member confirms or cancels; after
    that ``items`` is emptied and only the counts stay, as history. One live draft per member.
    """

    __tablename__ = "import_batch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bank: Mapped[str] = mapped_column(String(12), nullable=False)
    filename: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    rows_total: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_new: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_duplicate: Mapped[int] = mapped_column(nullable=False, default=0)
    rows_possible: Mapped[int] = mapped_column(nullable=False, default=0)
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PendingBatch(Base):
    """A draft of 2+ entries waiting for the member's confirmation (V2-17).

    One live draft per member (a newer one replaces the older). The row is deleted when
    confirmed or cancelled, so a second tap on the same button finds nothing.
    """

    __tablename__ = "pending_batch"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── V2-35 — training plan with loads, trip itinerary / packing / planned budget ──


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _member_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class WorkoutPlan(Base):
    """The member's weekly training plan. One active plan per member (older ones archived)."""

    __tablename__ = "workout_plan"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WorkoutPlanDay(Base):
    """One training day of a plan; ``weekday`` is 0=Monday..6=Sunday."""

    __tablename__ = "workout_plan_day"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    plan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workout_plan.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False, default="", server_default="")


class WorkoutPlanItem(Base):
    """An exercise in a plan day: sets x reps, optional planned load in kg."""

    __tablename__ = "workout_plan_item"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    day_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workout_plan_day.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    exercise: Mapped[str] = mapped_column(String(80), nullable=False)
    sets: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[str | None] = mapped_column(String(20))
    load_kg: Mapped[float | None] = mapped_column(Numeric(6, 2, asdecimal=False))


class WorkoutLoad(Base):
    """A load actually used on an exercise on a day; the history feeds the evolution."""

    __tablename__ = "workout_load"
    __table_args__ = (Index("ix_workout_load_member_ex_day", "member_id", "exercise_key", "day"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    exercise: Mapped[str] = mapped_column(String(80), nullable=False)
    exercise_key: Mapped[str] = mapped_column(String(80), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    load_kg: Mapped[float] = mapped_column(Numeric(6, 2, asdecimal=False), nullable=False)
    sets: Mapped[int | None] = mapped_column(Integer)
    reps: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TripItem(Base):
    """An itinerary entry of a trip (a day, optionally a time, and what happens)."""

    __tablename__ = "trip_item"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    at_time: Mapped[str | None] = mapped_column(String(5))
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TripPackItem(Base):
    """A packing-list item of a trip."""

    __tablename__ = "trip_pack_item"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    packed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class TripBudgetLine(Base):
    """Planned budget of a trip for one expense category (compared with what was spent)."""

    __tablename__ = "trip_budget_line"
    __table_args__ = (UniqueConstraint("trip_id", "category", name="uq_trip_budget_category"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    trip_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("trip.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)


class PendingAction(Base):
    """A draft waiting for the member's confirmation (training plan preview, ...).

    One live draft per member and ``kind``; deleted on confirm/cancel so a second tap is a no-op.
    """

    __tablename__ = "pending_action"

    id: Mapped[uuid.UUID] = _uuid_pk()
    member_id: Mapped[uuid.UUID] = _member_fk()
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReplyFeedback(Base):
    """V2-23 — one 👍/👎 on a reply. Counts and a closed-list reason only: never the reply text."""

    __tablename__ = "reply_feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(12), nullable=False)  # analysis | chat
    rating: Mapped[str] = mapped_column(String(4), nullable=False)  # up | down
    reason: Mapped[str | None] = mapped_column(String(12), nullable=True)  # wrong|unclear|missing
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ClientInvoice(Base):
    """V2-12 part 2 — an invoice the member issued to a client, tracked until it is paid.

    The app does not issue invoices: the member registers what they already sent. ``amount`` is
    the total including VAT. Paying one books a business income entry (``income_id``), so the
    accounting tab and the VAT quarter see it; undoing the payment removes that entry again.
    """

    __tablename__ = "client_invoice"
    __table_args__ = (UniqueConstraint("member_id", "number", name="uq_client_invoice_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client: Mapped[str] = mapped_column(String(60), nullable=False)
    number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=False)
    btw_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issued_on: Mapped[date] = mapped_column(Date, nullable=False)
    due_on: Mapped[date] = mapped_column(Date, nullable=False)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    income_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("expense.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ServiceRecord(Base):
    """V2-36 — a service (or later a product) the member paid for, with the dates worth tracking.

    ``status`` is ``draft`` until the member confirms (a draft expires after 15 minutes and one
    member has at most one). The invoice image is never stored. ``warranty_until`` is what the
    provider promised (computed by code); ``withdrawal_until`` exists only if the member said the
    contract was made online or outside a shop.
    """

    __tablename__ = "service_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False, default="service")
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="draft")
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(String(80), nullable=True)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    warranty_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    warranty_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    withdrawal_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    sent_w30: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_w7: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_wd: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HomeContract(Base):
    """V2-27 — an energy, gas or internet contract of the home, tracked to its end date.

    ``status`` is ``draft`` until the member confirms (15 minutes, one draft per member). One
    active contract per kind: confirming a new one replaces the old. ``sent_60`` / ``sent_30``
    mark the reminders already sent for the current ``ends_on`` (renewing resets them).
    """

    __tablename__ = "home_contract"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # energy | gas | internet
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="draft")
    provider: Mapped[str] = mapped_column(String(60), nullable=False)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2, asdecimal=False), nullable=True)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    sent_60: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_30: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
