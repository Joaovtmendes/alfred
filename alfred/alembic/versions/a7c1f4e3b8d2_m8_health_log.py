"""M8 — health_log table

Revision ID: a7c1f4e3b8d2
Revises: f2b4e1c8d3a7
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7c1f4e3b8d2"
down_revision = "f2b4e1c8d3a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("log_type", sa.String(50), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("unit", sa.String(50), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_health_log_member_id",
        "health_log",
        ["member_id"],
    )
    op.create_index(
        "ix_health_log_member_type",
        "health_log",
        ["member_id", "log_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_health_log_member_type", table_name="health_log")
    op.drop_index("ix_health_log_member_id", table_name="health_log")
    op.drop_table("health_log")
