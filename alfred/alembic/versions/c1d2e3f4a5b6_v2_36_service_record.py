"""v2-36 service records (warranty and cooling-off dates)

Revision ID: c1d2e3f4a5b6
Revises: b0c1d2e3f4a5
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "c1d2e3f4a5b6"
down_revision = "b0c1d2e3f4a5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_record",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("description", sa.String(80), nullable=True),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("warranty_months", sa.Integer(), nullable=True),
        sa.Column("warranty_until", sa.Date(), nullable=True),
        sa.Column("withdrawal_until", sa.Date(), nullable=True),
        sa.Column("sent_w30", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_w7", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_wd", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_service_record_member_id", "service_record", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_service_record_member_id", table_name="service_record")
    op.drop_table("service_record")
