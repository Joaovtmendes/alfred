"""habit_log: optional quantity and unit ("li 30 páginas")

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f0a1b2c3d4e5"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("habit_log", sa.Column("quantity", sa.Numeric(10, 2), nullable=True))
    op.add_column("habit_log", sa.Column("unit", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("habit_log", "unit")
    op.drop_column("habit_log", "quantity")
