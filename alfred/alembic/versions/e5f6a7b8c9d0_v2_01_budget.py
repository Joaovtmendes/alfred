"""V2-01 — budget (monthly cap per category)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "budget",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("member.id"), nullable=False
        ),
        sa.Column(
            "household_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("household.id"),
            nullable=False,
        ),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("monthly_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("last_alert_month", sa.Date(), nullable=True),
        sa.Column("last_alert_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("member_id", "category", name="uq_budget_member_category"),
    )
    op.create_index("ix_budget_member_id", "budget", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_budget_member_id", table_name="budget")
    op.drop_table("budget")
