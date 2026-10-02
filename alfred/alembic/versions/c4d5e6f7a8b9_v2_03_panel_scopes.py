"""V2-03 — panel v2: dashboard_v2 flag and single-use export token

Revision ID: c4d5e6f7a8b9
Revises: f2a3b4c5d6e7
Create Date: 2026-10-02
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c4d5e6f7a8b9"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member",
        sa.Column("dashboard_v2", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("member", sa.Column("export_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "member", sa.Column("export_token_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_member_export_token", "member", ["export_token"])


def downgrade() -> None:
    op.drop_constraint("uq_member_export_token", "member", type_="unique")
    op.drop_column("member", "export_token_expires_at")
    op.drop_column("member", "export_token")
    op.drop_column("member", "dashboard_v2")
