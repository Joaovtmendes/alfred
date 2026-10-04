"""V2-05 — statement import: import_batch, expense.source / external_id / import_batch_id

Existing entries are ``manual`` with no external id; the unique index only covers entries that have one.

Revision ID: e7f8a9b0c1d2
Revises: b2c3d4e5f6a7
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "e7f8a9b0c1d2"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "import_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("bank", sa.String(12), nullable=False),
        sa.Column("filename", sa.String(120)),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("rows_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_duplicate", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_possible", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_import_batch_member_id", "import_batch", ["member_id"])
    op.add_column(
        "expense",
        sa.Column("source", sa.String(10), nullable=False, server_default="manual"),
    )
    op.add_column("expense", sa.Column("external_id", sa.String(64), nullable=True))
    op.add_column(
        "expense",
        sa.Column(
            "import_batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("import_batch.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_expense_member_external",
        "expense",
        ["member_id", "external_id"],
        unique=True,
        postgresql_where=sa.text("external_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_expense_member_external", table_name="expense")
    op.drop_column("expense", "import_batch_id")
    op.drop_column("expense", "external_id")
    op.drop_column("expense", "source")
    op.drop_index("ix_import_batch_member_id", table_name="import_batch")
    op.drop_table("import_batch")
