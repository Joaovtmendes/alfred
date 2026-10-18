"""Google Calendar connection and appointment.google_event_id

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-18
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c7d8e9f0a1b2"
down_revision = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_connection",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("refresh_token", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(300), nullable=False, server_default=""),
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.add_column("appointment", sa.Column("google_event_id", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("appointment", "google_event_id")
    op.drop_table("google_connection")
