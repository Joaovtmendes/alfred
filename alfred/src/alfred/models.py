"""SQLAlchemy ORM models.

M1 — household / member / message (baseline)
M2 — expense table + preferred_name / language on member

Design decisions:
- All PKs are UUID generated server-side (no auto-increment)
- wa_message_id has a UNIQUE constraint → idempotency key for deduplication
- member.consent_state: pending | pending_response | accepted | rejected
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
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

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
    )  # pending | pending_response | accepted | rejected
    disclosure_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disclosure_version: Mapped[str | None] = mapped_column(String(20))

    # M11 — Dashboard
    dashboard_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, unique=True, default=None
    )
    dashboard_token_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    value: Mapped[str] = mapped_column(String(255), nullable=False)  # "omeprazol", "7", "6.5"
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # "/10", "hours", "mg", "L"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
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
