"""master schedules and time off

Revision ID: 0003_master_schedules
Revises: 0002_user_roles
Create Date: 2026-07-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_master_schedules"
down_revision: str | None = "0002_user_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "master_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "master_id",
            sa.Integer(),
            sa.ForeignKey("masters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.UniqueConstraint("master_id", "weekday", name="uq_schedule_master_weekday"),
    )

    op.create_table(
        "master_time_off",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "master_id",
            sa.Integer(),
            sa.ForeignKey("masters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_time_off_master_dates", "master_time_off", ["master_id", "date_from", "date_to"]
    )

    # Backfill: существующим мастерам — график по умолчанию (10:00–20:00, все дни),
    # чтобы поведение не изменилось; админ скорректирует через /admin.
    op.execute(
        """
        INSERT INTO master_schedules (master_id, weekday, start_time, end_time)
        SELECT m.id, d.weekday, TIME '10:00', TIME '20:00'
        FROM masters m
        CROSS JOIN generate_series(0, 6) AS d(weekday)
        """
    )


def downgrade() -> None:
    op.drop_table("master_time_off")
    op.drop_table("master_schedules")
