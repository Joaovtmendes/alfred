"""goal_contribution: money put aside for a financial goal

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-10-21
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e9f0a1b2c3d4"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "goal_contribution",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("member.id"), nullable=False
        ),
        sa.Column(
            "goal_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("goal.id"), nullable=False
        ),
        sa.Column("kind", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_goal_contribution_member_id", "goal_contribution", ["member_id"])
    op.create_index("ix_goal_contribution_goal_id", "goal_contribution", ["goal_id"])


def downgrade() -> None:
    op.drop_index("ix_goal_contribution_goal_id", table_name="goal_contribution")
    op.drop_index("ix_goal_contribution_member_id", table_name="goal_contribution")
    op.drop_table("goal_contribution")
