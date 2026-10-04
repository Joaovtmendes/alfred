"""S1-06 — health_log.value becomes Text (an encrypted token is longer than 255 characters)

Existing rows are left as they are; ``python -m alfred.crypto backfill`` encrypts them once
``DATA_ENCRYPTION_KEY`` is set. Reading handles both forms.

Revision ID: a1b2c3d4e5f6
Revises: d5e6f7a8b9c0
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("health_log", "value", existing_type=sa.String(255), type_=sa.Text())


def downgrade() -> None:
    # encrypted rows would not fit 255 characters: decrypt them with the backfill's inverse first
    op.alter_column(
        "health_log",
        "value",
        existing_type=sa.Text(),
        type_=sa.String(255),
        postgresql_using="left(value, 255)",
    )
