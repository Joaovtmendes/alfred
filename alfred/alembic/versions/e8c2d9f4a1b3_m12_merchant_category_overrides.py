"""M12 — merchant_category_overrides table for category learning.

Revision ID: e8c2d9f4a1b3
Revises: d9e4f2a1b7c8
Create Date: 2026-09-26 10:00:00.000000
"""
from __future__ import annotations

import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "e8c2d9f4a1b3"
down_revision = "d9e4f2a1b7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "merchant_category_overrides",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            default=uuid.uuid4,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("merchant", sa.String(255), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("member_id", "merchant", name="uq_mco_member_merchant"),
    )
    op.create_index(
        "ix_mco_member_merchant",
        "merchant_category_overrides",
        ["member_id", "merchant"],
    )


def downgrade() -> None:
    op.drop_index("ix_mco_member_merchant", table_name="merchant_category_overrides")
    op.drop_table("merchant_category_overrides")
