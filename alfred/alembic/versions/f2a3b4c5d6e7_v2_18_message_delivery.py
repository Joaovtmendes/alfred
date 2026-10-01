"""V2-18 — message.kind / template_name / delivery_status / delivery_error

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("message", sa.Column("kind", sa.String(12), nullable=True))
    op.add_column("message", sa.Column("template_name", sa.String(64), nullable=True))
    op.add_column("message", sa.Column("delivery_status", sa.String(10), nullable=True))
    op.add_column("message", sa.Column("delivery_error", sa.String(200), nullable=True))
    # Existing outbound rows were all replies; their delivery state is unknown (stays NULL).
    op.execute("UPDATE message SET kind='reply' WHERE direction='outbound'")


def downgrade() -> None:
    for col in ("delivery_error", "delivery_status", "template_name", "kind"):
        op.drop_column("message", col)
