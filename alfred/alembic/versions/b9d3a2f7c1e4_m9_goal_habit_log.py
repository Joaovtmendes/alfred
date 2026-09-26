"""M9 — goal + habit_log tables

Revision ID: b9d3a2f7c1e4
Revises: a7c1f4e3b8d2
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b9d3a2f7c1e4"
down_revision = "a7c1f4e3b8d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("target_value", sa.String(100), nullable=True),
        sa.Column("target_unit", sa.String(50), nullable=True),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_goal_member_id", "goal", ["member_id"])

    op.create_table(
        "habit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goal.id"),
            nullable=True,
        ),
        sa.Column("activity", sa.String(255), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_habit_log_member_id", "habit_log", ["member_id"])
    op.create_index("ix_habit_log_goal_id", "habit_log", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_habit_log_goal_id", table_name="habit_log")
    op.drop_index("ix_habit_log_member_id", table_name="habit_log")
    op.drop_table("habit_log")
    op.drop_index("ix_goal_member_id", table_name="goal")
    op.drop_table("goal")
