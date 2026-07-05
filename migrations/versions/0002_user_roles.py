"""user roles and master↔user link

Revision ID: 0002_user_roles
Revises: 0001_initial
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_user_roles"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=16), nullable=False, server_default="client"),
    )
    op.create_index("ix_users_role", "users", ["role"])

    op.add_column(
        "masters",
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "masters",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.create_foreign_key(
        "fk_masters_user_id",
        "masters",
        "users",
        ["user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_masters_user_id", "masters", ["user_id"])


def downgrade() -> None:
    op.drop_constraint("uq_masters_user_id", "masters", type_="unique")
    op.drop_constraint("fk_masters_user_id", "masters", type_="foreignkey")
    op.drop_column("masters", "is_active")
    op.drop_column("masters", "user_id")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
