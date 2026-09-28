"""Sprint 1 — exact money, missing indexes, trip budget

* expense.amount FLOAT -> NUMERIC(12,2)   (exact cents; existing values rounded to 2 dp)
* indexes: expense(member_id, expense_date), message(author_id, created_at), member(household_id)
* trip.budget NUMERIC(12,2) NULL          (hard test BUG-07: "saldo restante")

Revision ID: a1d5c7e9b3f2
Revises: e5f3a2d7c8b1
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1d5c7e9b3f2"
down_revision = "e5f3a2d7c8b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "expense",
        "amount",
        existing_type=sa.Float(),
        type_=sa.Numeric(12, 2),
        existing_nullable=False,
        postgresql_using="round(amount::numeric, 2)",
    )
    op.add_column("trip", sa.Column("budget", sa.Numeric(12, 2), nullable=True))
    op.create_index("ix_expense_member_date", "expense", ["member_id", "expense_date"])
    op.create_index("ix_message_author_created", "message", ["author_id", "created_at"])
    op.create_index("ix_member_household_id", "member", ["household_id"])


def downgrade() -> None:
    op.drop_index("ix_member_household_id", table_name="member")
    op.drop_index("ix_message_author_created", table_name="message")
    op.drop_index("ix_expense_member_date", table_name="expense")
    op.drop_column("trip", "budget")
    op.alter_column(
        "expense",
        "amount",
        existing_type=sa.Numeric(12, 2),
        type_=sa.Float(),
        existing_nullable=False,
        postgresql_using="amount::double precision",
    )
