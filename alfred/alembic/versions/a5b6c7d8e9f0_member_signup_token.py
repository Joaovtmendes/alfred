"""member.signup_token (single-use link of the mandatory sign-up page)

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "a5b6c7d8e9f0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("member", sa.Column("signup_token", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "member", sa.Column("signup_token_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_unique_constraint("uq_member_signup_token", "member", ["signup_token"])


def downgrade() -> None:
    op.drop_constraint("uq_member_signup_token", "member", type_="unique")
    op.drop_column("member", "signup_token_expires_at")
    op.drop_column("member", "signup_token")
