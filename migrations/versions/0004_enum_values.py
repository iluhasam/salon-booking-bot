"""store enum values (lowercase) instead of member names

Revision ID: 0004_enum_values
Revises: 0003_master_schedules
Create Date: 2026-07-05

До этой ревизии ORM записывал имена членов enum ('CONFIRMED', 'CLIENT'),
а server_default миграций и EXCLUDE-констрейнт uq_bookings_no_overlap
использовали значения ('confirmed', 'client'): констрейнт молча не
покрывал такие строки, а чтение 'client' из server_default падало.
Приводим существующие данные к значениям (lowercase); ORM теперь пишет
их же (values_callable в app/db/models.py).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_enum_values"
down_revision: str | None = "0003_master_schedules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = lower(role) WHERE role <> lower(role)")
    op.execute("UPDATE bookings SET status = lower(status) WHERE status <> lower(status)")


def downgrade() -> None:
    # Обратное преобразование не требуется: значения в нижнем регистре
    # корректно читались и старым кодом только для users.role по умолчанию;
    # намеренно no-op.
    pass
