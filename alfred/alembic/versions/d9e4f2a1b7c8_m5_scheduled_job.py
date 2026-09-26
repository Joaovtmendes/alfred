"""M5 — scheduled_job table for proactive notifications.

Revision ID: d9e4f2a1b7c8
Revises: c3f1b8e2a7d5
Create Date: 2026-09-26 09:00:00.000000
"""
from __future__ import annotations

import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "d9e4f2a1b7c8"
down_revision = "c3f1b8e2a7d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_job",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("job_type", sa.String(50), nullable=False),
        sa.Column("time_of_day", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("days_mask", sa.Integer(), nullable=False, server_default="127"),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_scheduled_job_member_active",
        "scheduled_job",
        ["member_id", "active"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_job_member_active", table_name="scheduled_job")
    op.drop_table("scheduled_job")
