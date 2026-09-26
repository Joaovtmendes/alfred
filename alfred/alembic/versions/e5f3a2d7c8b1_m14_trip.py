"""M14 — trip table + trip_id on expense

Revision ID: e5f3a2d7c8b1
Revises: d1f2e3a4b5c6
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e5f3a2d7c8b1"
down_revision = "d1f2e3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("destination", sa.String(200), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=False),
        sa.Column("ended_at", sa.Date(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_trip_member_id", "trip", ["member_id"])
    op.create_index("ix_trip_member_active", "trip", ["member_id", "active"])

    op.add_column(
        "expense",
        sa.Column(
            "trip_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trip.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_expense_trip_id", "expense", ["trip_id"])


def downgrade() -> None:
    op.drop_index("ix_expense_trip_id", table_name="expense")
    op.drop_column("expense", "trip_id")
    op.drop_index("ix_trip_member_active", table_name="trip")
    op.drop_index("ix_trip_member_id", table_name="trip")
    op.drop_table("trip")
