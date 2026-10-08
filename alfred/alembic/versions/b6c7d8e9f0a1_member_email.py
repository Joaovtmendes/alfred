"""member.email (asked on the sign-up page)

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b6c7d8e9f0a1"
down_revision = "a5b6c7d8e9f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("member", sa.Column("email", sa.String(254), nullable=True))


def downgrade() -> None:
    op.drop_column("member", "email")
