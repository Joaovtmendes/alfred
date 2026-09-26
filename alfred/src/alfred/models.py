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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    members: Mapped[list[Member]] = relationship(back_populates="household")
    messages: Mapped[list[Message]] = relationship(back_populates="household")
    expenses: Mapped[list[Expense]] = relationship(back_populates="household")


class Member(Base):
    __tablename__ = "member"
    __table_args__ = (UniqueConstraint("wa_phone", name="uq_member_wa_phone"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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
    disclosure_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    disclosure_version: Mapped[str | None] = mapped_column(String(20))

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
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    wa_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id")
    )
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False
    )  # inbound | outbound
    body: Mapped[str | None] = mapped_column(Text)
    wa_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    raw: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped[Household] = relationship(back_populates="messages")
    author: Mapped[Member | None] = relationship(back_populates="messages")


class Expense(Base):
    """A financial transaction recorded by a member via natural language."""

    __tablename__ = "expense"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False
    )
    household_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("household.id"), nullable=False
    )

    # expense | income
    transaction_type: Mapped[str] = mapped_column(
        String(10), nullable=False, default="expense"
    )

    amount: Mapped[float] = mapped_column(Float, nullable=False)
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

    member: Mapped[Member] = relationship(back_populates="expenses")
    household: Mapped[Household] = relationship(back_populates="expenses")


class ScheduledJob(Base):
    """Proactive notification schedule for a member.

    M5 — each row represents one recurring reminder or weekly summary.
    The daily cron script queries this table for due jobs and enqueues them.

    job_type:
        weekly_summary        — sent every Monday morning
        medication_reminder   — sent daily at time_of_day
        goal_checkin          — sent daily at time_of_day
        workout_reminder      — sent daily at time_of_day

    days_mask: bitmask Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64
               127 = every day, 1 = Monday only
    """

    __tablename__ = "scheduled_job"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    job_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # weekly_summary | medication_reminder | goal_checkin | workout_reminder
    time_of_day: Mapped[str] = mapped_column(
        String(5), nullable=False, default="08:00"
    )  # "HH:MM" UTC
    days_mask: Mapped[int] = mapped_column(
        nullable=False, default=127
    )  # bitmask; 127 = every day
    payload: Mapped[dict | None] = mapped_column(JSONB)  # extra params e.g. {"text": "toma medicamento"}
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship()


class MerchantCategoryOverride(Base):
    """M12 — User-taught category corrections.

    When a member corrects Alfred's category for a merchant
    (e.g. "Jumbo is supermarkt, not restaurant"), Alfred stores the override
    here and applies it automatically the next time that merchant appears.

    merchant is normalised to lowercase for case-insensitive matching.
    Unique constraint: one override per (member_id, merchant) pair.
    """

    __tablename__ = "merchant_category_overrides"
    __table_args__ = (
        UniqueConstraint(
            "member_id", "merchant", name="uq_mco_member_merchant"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    merchant: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # normalised lowercase
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

    member: Mapped["Member"] = relationship()


# ── M7 — Treino ─────────────────────────────────────────────────────────────

class WorkoutSession(Base):
    """Registo de uma sessão de treino/exercício."""

    __tablename__ = "workout_session"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    member: Mapped["Member"] = relationship()


# ── M8 — Saúde ──────────────────────────────────────────────────────────────

class HealthLog(Base):
    """Registo de saúde: medicação, humor, sono, água."""

    __tablename__ = "health_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    log_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # medication | mood | sleep | water
    value: Mapped[str] = mapped_column(
        String(255), nullable=False
    )  # "omeprazol", "7", "6.5"
    unit: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "/10", "hours", "mg", "L"
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship()


# ── M9 — Metas & Hábitos ────────────────────────────────────────────────────

class Goal(Base):
    """Meta pessoal do utilizador."""

    __tablename__ = "goal"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    target_value: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # "500", "3x"
    target_unit: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "EUR/month", "per week"
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship()
    habit_logs: Mapped[list["HabitLog"]] = relationship(back_populates="goal")


class HabitLog(Base):
    """Registo diário de um hábito ligado a uma meta."""

    __tablename__ = "habit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
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

    member: Mapped["Member"] = relationship()
    goal: Mapped["Goal | None"] = relationship(back_populates="habit_logs")


# ── M10 — Produtividade ──────────────────────────────────────────────────────

class Note(Base):
    """Nota rápida do utilizador."""

    __tablename__ = "note"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship()


class Task(Base):
    """Tarefa do utilizador, com prazo e estado de conclusão."""

    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("member.id"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(String(500), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    member: Mapped["Member"] = relationship()
