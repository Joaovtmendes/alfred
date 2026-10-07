"""v2-27 home contracts (energy, gas, internet)

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "home_contract",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "member_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("sent_60", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sent_30", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_home_contract_member_id", "home_contract", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_home_contract_member_id", table_name="home_contract")
    op.drop_table("home_contract")
