"""Репозиторий графиков работы и отпусков мастеров."""

from __future__ import annotations

import logging
from datetime import date, time

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import MasterSchedule, MasterTimeOff

logger = logging.getLogger(__name__)

WEEKDAYS_RU = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


class ScheduleRepository:
    """Рабочие часы по дням недели и интервалы недоступности."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------- график ----------

    async def get_week(self, master_id: int) -> dict[int, tuple[time, time]]:
        """График недели: {weekday: (start, end)}; отсутствие дня = выходной."""
        rows = await self._session.scalars(
            select(MasterSchedule).where(MasterSchedule.master_id == master_id)
        )
        return {row.weekday: (row.start_time, row.end_time) for row in rows}

    async def set_day(self, master_id: int, weekday: int, start: time, end: time) -> None:
        """Задаёт рабочие часы дня недели (создаёт или обновляет)."""
        row = await self._session.scalar(
            select(MasterSchedule).where(
                MasterSchedule.master_id == master_id,
                MasterSchedule.weekday == weekday,
            )
        )
        if row is None:
            self._session.add(
                MasterSchedule(
                    master_id=master_id, weekday=weekday, start_time=start, end_time=end
                )
            )
        else:
            row.start_time, row.end_time = start, end
        await self._session.flush()
        logger.info("Schedule set: master=%s wd=%s %s-%s", master_id, weekday, start, end)

    async def clear_day(self, master_id: int, weekday: int) -> None:
        """Делает день недели выходным."""
        await self._session.execute(
            delete(MasterSchedule).where(
                MasterSchedule.master_id == master_id,
                MasterSchedule.weekday == weekday,
            )
        )
        await self._session.flush()
        logger.info("Schedule cleared: master=%s wd=%s", master_id, weekday)

    async def create_default_week(self, master_id: int) -> None:
        """График по умолчанию (часы салона, все дни) — при создании мастера."""
        settings = get_settings()
        existing = await self.get_week(master_id)
        for weekday in range(7):
            if weekday not in existing:
                self._session.add(
                    MasterSchedule(
                        master_id=master_id,
                        weekday=weekday,
                        start_time=settings.work_start,
                        end_time=settings.work_end,
                    )
                )
        await self._session.flush()

    # ---------- отпуска ----------

    async def add_time_off(
        self, master_id: int, date_from: date, date_to: date, reason: str | None = None
    ) -> MasterTimeOff:
        row = MasterTimeOff(
            master_id=master_id, date_from=date_from, date_to=date_to, reason=reason
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("Time off added: master=%s %s..%s", master_id, date_from, date_to)
        return row

    async def list_time_off(self, master_id: int, from_day: date) -> list[MasterTimeOff]:
        """Актуальные (не закончившиеся) интервалы недоступности."""
        rows = await self._session.scalars(
            select(MasterTimeOff)
            .where(MasterTimeOff.master_id == master_id, MasterTimeOff.date_to >= from_day)
            .order_by(MasterTimeOff.date_from)
        )
        return list(rows)

    async def delete_time_off(self, time_off_id: int) -> bool:
        row = await self._session.get(MasterTimeOff, time_off_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        logger.info("Time off removed: id=%s master=%s", time_off_id, row.master_id)
        return True

    # ---------- доступность ----------

    async def get_working_hours(self, master_id: int, day: date) -> tuple[time, time] | None:
        """Часы работы мастера в конкретную дату.

        None — выходной по графику или попадание в отпуск.
        """
        in_time_off = await self._session.scalar(
            select(MasterTimeOff.id).where(
                MasterTimeOff.master_id == master_id,
                MasterTimeOff.date_from <= day,
                MasterTimeOff.date_to >= day,
            )
        )
        if in_time_off is not None:
            return None

        row = await self._session.scalar(
            select(MasterSchedule).where(
                MasterSchedule.master_id == master_id,
                MasterSchedule.weekday == day.weekday(),
            )
        )
        if row is None:
            return None
        return (row.start_time, row.end_time)
