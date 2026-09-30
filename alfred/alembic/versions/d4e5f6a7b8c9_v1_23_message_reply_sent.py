"""V1-23 — message.reply_sent (a failed handler run already answered the user)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message",
        sa.Column("reply_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("message", "reply_sent")
