"""v2-23 reply feedback (thumbs up/down, counts and a closed-list reason only)

Revision ID: a9b0c1d2e3f4
Revises: f8a9b0c1d2e3
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a9b0c1d2e3f4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reply_feedback",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(12), nullable=False),
        sa.Column("rating", sa.String(4), nullable=False),
        sa.Column("reason", sa.String(12), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_reply_feedback_member_id", "reply_feedback", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_reply_feedback_member_id", table_name="reply_feedback")
    op.drop_table("reply_feedback")
