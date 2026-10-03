"""V2-35 — training plan with loads, trip itinerary / packing / planned budget, pending action

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-10-03
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)


def _pk() -> sa.Column:
    return sa.Column("id", _UUID, primary_key=True)


def _member() -> sa.Column:
    return sa.Column(
        "member_id", _UUID, sa.ForeignKey("member.id", ondelete="CASCADE"), nullable=False
    )


def _created() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "workout_plan",
        _pk(),
        _member(),
        sa.Column("name", sa.String(80), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _created(),
    )
    op.create_index("ix_workout_plan_member_id", "workout_plan", ["member_id"])
    op.create_table(
        "workout_plan_day",
        _pk(),
        _member(),
        sa.Column(
            "plan_id", _UUID, sa.ForeignKey("workout_plan.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(80), nullable=False, server_default=""),
    )
    op.create_index("ix_workout_plan_day_member_id", "workout_plan_day", ["member_id"])
    op.create_index("ix_workout_plan_day_plan_id", "workout_plan_day", ["plan_id"])
    op.create_table(
        "workout_plan_item",
        _pk(),
        _member(),
        sa.Column(
            "day_id",
            _UUID,
            sa.ForeignKey("workout_plan_day.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exercise", sa.String(80), nullable=False),
        sa.Column("sets", sa.Integer()),
        sa.Column("reps", sa.String(20)),
        sa.Column("load_kg", sa.Numeric(6, 2)),
    )
    op.create_index("ix_workout_plan_item_member_id", "workout_plan_item", ["member_id"])
    op.create_index("ix_workout_plan_item_day_id", "workout_plan_item", ["day_id"])
    op.create_table(
        "workout_load",
        _pk(),
        _member(),
        sa.Column("exercise", sa.String(80), nullable=False),
        sa.Column("exercise_key", sa.String(80), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("load_kg", sa.Numeric(6, 2), nullable=False),
        sa.Column("sets", sa.Integer()),
        sa.Column("reps", sa.String(20)),
        _created(),
    )
    op.create_index("ix_workout_load_member_id", "workout_load", ["member_id"])
    op.create_index(
        "ix_workout_load_member_ex_day", "workout_load", ["member_id", "exercise_key", "day"]
    )
    for table, cols in (
        (
            "trip_item",
            [
                sa.Column("day", sa.Date(), nullable=False),
                sa.Column("at_time", sa.String(5)),
                sa.Column("title", sa.String(160), nullable=False),
            ],
        ),
        (
            "trip_pack_item",
            [
                sa.Column("name", sa.String(120), nullable=False),
                sa.Column("packed", sa.Boolean(), nullable=False, server_default=sa.false()),
            ],
        ),
    ):
        op.create_table(
            table,
            _pk(),
            _member(),
            sa.Column(
                "trip_id", _UUID, sa.ForeignKey("trip.id", ondelete="CASCADE"), nullable=False
            ),
            *cols,
            _created(),
        )
        op.create_index(f"ix_{table}_member_id", table, ["member_id"])
        op.create_index(f"ix_{table}_trip_id", table, ["trip_id"])
    op.create_table(
        "trip_budget_line",
        _pk(),
        _member(),
        sa.Column("trip_id", _UUID, sa.ForeignKey("trip.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.UniqueConstraint("trip_id", "category", name="uq_trip_budget_category"),
    )
    op.create_index("ix_trip_budget_line_member_id", "trip_budget_line", ["member_id"])
    op.create_index("ix_trip_budget_line_trip_id", "trip_budget_line", ["trip_id"])
    op.create_table(
        "pending_action",
        _pk(),
        _member(),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created(),
    )
    op.create_index("ix_pending_action_member_id", "pending_action", ["member_id"])


def downgrade() -> None:
    for table in (
        "pending_action",
        "trip_budget_line",
        "trip_pack_item",
        "trip_item",
        "workout_load",
        "workout_plan_item",
        "workout_plan_day",
        "workout_plan",
    ):
        op.drop_table(table)
