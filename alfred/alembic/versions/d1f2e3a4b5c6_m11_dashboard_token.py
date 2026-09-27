"""M11 — dashboard_token on member

Revision ID: d1f2e3a4b5c6
Revises: c4e8b1a3f7d2
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d1f2e3a4b5c6"
down_revision = "c4e8b1a3f7d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member",
        sa.Column(
            "dashboard_token",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_unique_constraint(
        "uq_member_dashboard_token", "member", ["dashboard_token"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_member_dashboard_token", "member", type_="unique")
    op.drop_column("member", "dashboard_token")
