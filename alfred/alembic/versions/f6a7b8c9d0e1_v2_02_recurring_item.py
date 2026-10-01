"""V2-02 — recurring_item (contas fixas) and expense.status

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense",
        sa.Column("status", sa.String(12), nullable=False, server_default="paid"),
    )
    op.create_table(
        "recurring_item",
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
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("kind", sa.String(14), nullable=False, server_default="fixed"),
        sa.Column("frequency", sa.String(10), nullable=False, server_default="monthly"),
        sa.Column("due_day", sa.Integer(), nullable=True),
        sa.Column("next_due_date", sa.Date(), nullable=False),
        sa.Column("installments_total", sa.Integer(), nullable=True),
        sa.Column("installments_paid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("remind_days_before", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_reminded_for", sa.Date(), nullable=True),
        sa.Column("contract_end_date", sa.Date(), nullable=True),
        sa.Column("provider", sa.String(120), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_recurring_member_active", "recurring_item", ["member_id", "active"])


def downgrade() -> None:
    op.drop_index("ix_recurring_member_active", table_name="recurring_item")
    op.drop_table("recurring_item")
    op.drop_column("expense", "status")
