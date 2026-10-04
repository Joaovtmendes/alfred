"""V2-10 — couple mode: partner_link, partner_settlement, expense.shared, member.home_categories

Everything is personal by default (``shared`` false, no link): existing members are unaffected.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-10-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense",
        sa.Column("shared", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "member",
        sa.Column("home_categories", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "partner_link",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inviter_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "partner_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("code", sa.String(8), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("inviter_pct", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("code", name="uq_partner_link_code"),
    )
    op.create_index("ix_partner_link_inviter", "partner_link", ["inviter_id"])
    op.create_index("ix_partner_link_partner", "partner_link", ["partner_id"])
    op.create_table(
        "partner_settlement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("partner_link.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_partner_settlement_link_id", "partner_settlement", ["link_id"])


def downgrade() -> None:
    op.drop_index("ix_partner_settlement_link_id", table_name="partner_settlement")
    op.drop_table("partner_settlement")
    op.drop_index("ix_partner_link_partner", table_name="partner_link")
    op.drop_index("ix_partner_link_inviter", table_name="partner_link")
    op.drop_table("partner_link")
    op.drop_column("member", "home_categories")
    op.drop_column("expense", "shared")
