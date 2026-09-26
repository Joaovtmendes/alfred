"""M10 — note + task tables

Revision ID: c4e8b1a3f7d2
Revises: b9d3a2f7c1e4
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8b1a3f7d2"
down_revision = "b9d3a2f7c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "note",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_note_member_id", "note", ["member_id"])

    op.create_table(
        "task",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id"),
            nullable=False,
        ),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_task_member_id", "task", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_task_member_id", table_name="task")
    op.drop_table("task")
    op.drop_index("ix_note_member_id", table_name="note")
    op.drop_table("note")
