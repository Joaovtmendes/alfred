"""SQLAlchemy ORM models — M1 baseline (household / member / message).

Design decisions:
- All PKs are UUID generated server-side (no auto-increment)
- wa_message_id has a UNIQUE constraint → idempotency key for deduplication
- member.consent_state: pending | accepted | rejected
- message.direction: inbound | outbound
- All timestamps in UTC (timestamptz)
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
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

    # AVG consent + EU AI Act Art. 50 disclosure
    consent_state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending | accepted | rejected
    disclosure_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    disclosure_version: Mapped[str | None] = mapped_column(String(20))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped[Household] = relationship(back_populates="members")
    messages: Mapped[list[Message]] = relationship(back_populates="author")


class Message(Base):
    """Inbound and outbound WhatsApp messages.

    wa_message_id is the Meta-assigned ID — unique constraint ensures
    idempotency: re-delivered webhooks are silently ignored via INSERT … ON CONFLICT DO NOTHING.
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
    raw: Mapped[dict | None] = mapped_column(JSONB)  # full Meta payload
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    household: Mapped[Household] = relationship(back_populates="messages")
    author: Mapped[Member | None] = relationship(back_populates="messages")
