"""V2-14 — saved_view (analysis specs)

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_view",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(40), nullable=True),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_saved_view_member", "saved_view", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_view_member", table_name="saved_view")
    op.drop_table("saved_view")
