"""member.timezone (A-66: each person's own timezone)

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d8e9f0a1b2c3"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("member", sa.Column("timezone", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("member", "timezone")
