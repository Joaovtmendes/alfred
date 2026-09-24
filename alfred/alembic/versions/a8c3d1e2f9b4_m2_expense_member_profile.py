"""M2 — expense table + preferred_name / language on member.

Revision ID: a8c3d1e2f9b4
Revises: 4591ef6c363b
Create Date: 2026-09-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a8c3d1e2f9b4"
down_revision = "4591ef6c363b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. New columns on member ─────────────────────────────────────────────
    op.add_column(
        "member",
        sa.Column("preferred_name", sa.String(100), nullable=True),
    )
    op.add_column(
        "member",
        sa.Column("language", sa.String(10), nullable=False, server_default="pt"),
    )

    # ── 2. New expense table ─────────────────────────────────────────────────
    op.create_table(
        "expense",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column(
            "household_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("household.id"),
            nullable=False,
        ),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="EUR"),
        sa.Column("merchant", sa.String(255), nullable=True),
        sa.Column("category", sa.String(50), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "expense_date",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_expense_member_id", "expense", ["member_id"])
    op.create_index("ix_expense_household_id", "expense", ["household_id"])
    op.create_index("ix_expense_expense_date", "expense", ["expense_date"])


def downgrade() -> None:
    op.drop_index("ix_expense_expense_date", table_name="expense")
    op.drop_index("ix_expense_household_id", table_name="expense")
    op.drop_index("ix_expense_member_id", table_name="expense")
    op.drop_table("expense")
    op.drop_column("member", "language")
    op.drop_column("member", "preferred_name")
