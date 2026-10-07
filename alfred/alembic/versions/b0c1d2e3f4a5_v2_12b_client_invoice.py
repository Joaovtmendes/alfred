"""v2-12 part 2 client invoices (receivables tracking)

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_invoice",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("client", sa.String(60), nullable=False),
        sa.Column("number", sa.String(20), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("btw_rate", sa.Integer(), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("paid_on", sa.Date(), nullable=True),
        sa.Column(
            "income_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("expense.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("member_id", "number", name="uq_client_invoice_number"),
    )
    op.create_index("ix_client_invoice_member_id", "client_invoice", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_client_invoice_member_id", table_name="client_invoice")
    op.drop_table("client_invoice")
