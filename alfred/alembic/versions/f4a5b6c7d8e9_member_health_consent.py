"""member.health_consent_at (art. 9 consent before storing health data)

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "member", sa.Column("health_consent_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Members who already logged health data keep using it: their earlier messages are the
    # consent the old flow relied on. They are asked again only if they withdraw.
    op.execute(
        "UPDATE member SET health_consent_at = now() "
        "WHERE id IN (SELECT DISTINCT member_id FROM health_log)"
    )


def downgrade() -> None:
    op.drop_column("member", "health_consent_at")
