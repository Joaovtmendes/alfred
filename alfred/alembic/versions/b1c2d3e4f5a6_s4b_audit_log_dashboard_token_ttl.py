"""S4b — audit_log table + member.dashboard_token_created_at (token expiry)

Existing tokens get ``now()`` as their creation time, so nobody's link dies on deploy day;
they expire ``dashboard_token_ttl_days`` later.

Revision ID: b1c2d3e4f5a6
Revises: a1d5c7e9b3f2
Create Date: 2026-09-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a1d5c7e9b3f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member", sa.Column("dashboard_token_created_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE member SET dashboard_token_created_at = now() WHERE dashboard_token IS NOT NULL"
    )
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("detail", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_log_member_id", "audit_log", ["member_id"])
    op.create_index("ix_audit_log_event", "audit_log", ["event"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_event", table_name="audit_log")
    op.drop_index("ix_audit_log_member_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_column("member", "dashboard_token_created_at")
