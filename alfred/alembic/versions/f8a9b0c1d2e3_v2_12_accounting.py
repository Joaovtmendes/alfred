"""V2-12 — accounting tab: member.acct_mode / business_categories / tax_reserve_pct / savings_amount,
expense.scope / btw_rate / deductible

Existing members stay in personal mode and existing entries stay personal.

Revision ID: f8a9b0c1d2e3
Revises: e7f8a9b0c1d2
Create Date: 2026-10-06
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "f8a9b0c1d2e3"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member",
        sa.Column("acct_mode", sa.String(10), nullable=False, server_default="personal"),
    )
    op.add_column("member", sa.Column("business_categories", postgresql.JSONB(), nullable=True))
    op.add_column("member", sa.Column("tax_reserve_pct", sa.Integer(), nullable=True))
    op.add_column("member", sa.Column("savings_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "expense", sa.Column("scope", sa.String(10), nullable=False, server_default="personal")
    )
    op.add_column("expense", sa.Column("btw_rate", sa.Integer(), nullable=True))
    op.add_column(
        "expense", sa.Column("deductible", sa.Boolean(), nullable=False, server_default="true")
    )


def downgrade() -> None:
    for col in ("deductible", "btw_rate", "scope"):
        op.drop_column("expense", col)
    for col in ("savings_amount", "tax_reserve_pct", "business_categories", "acct_mode"):
        op.drop_column("member", col)
