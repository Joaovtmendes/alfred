"""V2-16 — settled income is "received" (every other existing row stays "paid")

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-10-01
"""

from __future__ import annotations

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE expense SET status = 'received' WHERE transaction_type = 'income' AND status = 'paid'"
    )


def downgrade() -> None:
    op.execute("UPDATE expense SET status = 'paid' WHERE status = 'received'")
