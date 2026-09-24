"""M4a — add transaction_type to expense (expense | income).

Revision ID: c3f1b8e2a7d5
Revises: a8c3d1e2f9b4
Create Date: 2026-09-24
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c3f1b8e2a7d5"
down_revision = "a8c3d1e2f9b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense",
        sa.Column(
            "transaction_type",
            sa.String(10),
            nullable=False,
            server_default="expense",
        ),
    )


def downgrade() -> None:
    op.drop_column("expense", "transaction_type")
