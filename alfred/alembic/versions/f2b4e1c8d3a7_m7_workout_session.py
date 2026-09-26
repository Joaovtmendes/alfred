"""M7 — workout_session table

Revision ID: f2b4e1c8d3a7
Revises: e8c2d9f4a1b3
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f2b4e1c8d3a7"
down_revision = "e8c2d9f4a1b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workout_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("activity_type", sa.String(100), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_workout_session_member_id",
        "workout_session",
        ["member_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_workout_session_member_id", table_name="workout_session")
    op.drop_table("workout_session")
