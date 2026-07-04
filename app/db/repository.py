"""Бизнес-логика работы с БД (отделена от слоя интерфейса).

Все методы принимают `AsyncSession` извне — управление жизненным циклом
сессии и транзакции лежит на вызывающем коде (middleware / задача Taskiq).

Защита от гонок при бронировании — двухуровневая:
1. `pg_advisory_xact_lock(master_id)` сериализует конкурентные брони к одному
   мастеру: проверка пересечений и INSERT выполняются атомарно.
2. EXCLUDE-констрейнт `uq_bookings_no_overlap` в PostgreSQL — страховка на
   уровне БД: пересекающиеся интервалы физически не могут быть записаны
   (IntegrityError транслируется в SlotOccupiedError).
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import Booking, BookingStatus, Master, Service, User

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


class SlotOccupiedError(Exception):
    """Слот пересекается с существующей записью."""


class BookingRepository:
    """Репозиторий бронирований."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------- чтение ----------

    async def get_services(self) -> list[Service]:
        """Все услуги."""
        result = await self._session.scalars(select(Service).order_by(Service.title))
        return list(result)

    async def get_masters(self) -> list[Master]:
        """Активные мастера, отсортированные по рейтингу (для клиентов)."""
        result = await self._session.scalars(
            select(Master).where(Master.is_active.is_(True)).order_by(Master.rating.desc())
        )
        return list(result)

    async def get_upcoming_bookings(
        self, master_id: int | None = None, limit: int = 30
    ) -> list[Booking]:
        """Будущие активные записи: все (админ) или одного мастера (/schedule)."""
        stmt = (
            select(Booking)
            .where(
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.starts_at >= datetime.now(UTC),
            )
            .options(
                selectinload(Booking.service),
                selectinload(Booking.master),
                selectinload(Booking.user),
            )
            .order_by(Booking.starts_at)
            .limit(limit)
        )
        if master_id is not None:
            stmt = stmt.where(Booking.master_id == master_id)
        return list(await self._session.scalars(stmt))

    async def get_service(self, service_id: int) -> Service | None:
        return await self._session.get(Service, service_id)

    async def get_master(self, master_id: int) -> Master | None:
        return await self._session.get(Master, master_id)

    async def get_booking(self, booking_id: int) -> Booking | None:
        return await self._session.get(Booking, booking_id)

    async def get_user_bookings(self, telegram_id: int) -> list[Booking]:
        """Будущие активные записи пользователя (с услугой и мастером)."""
        now = datetime.now(UTC)
        result = await self._session.scalars(
            select(Booking)
            .join(User, Booking.user_id == User.id)
            .where(
                User.telegram_id == telegram_id,
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.starts_at >= now,
            )
            .options(selectinload(Booking.service), selectinload(Booking.master))
            .order_by(Booking.starts_at)
        )
        return list(result)

    async def get_available_slots(
        self, master_id: int, day: date, duration_minutes: int
    ) -> list[datetime]:
        """Свободные слоты мастера на день с учётом длительности услуги.

        Слот считается занятым, если интервал [slot, slot + duration)
        пересекается с интервалом любой активной записи.
        """
        settings = get_settings()
        tz = settings.tz

        cursor = datetime.combine(day, settings.work_start, tzinfo=tz)
        end_of_day = datetime.combine(day, settings.work_end, tzinfo=tz)
        duration = timedelta(minutes=duration_minutes)
        now = datetime.now(tz)

        busy = await self._get_busy_intervals(master_id, cursor, end_of_day)

        slots: list[datetime] = []
        while cursor + duration <= end_of_day:
            if cursor > now and not self._overlaps(cursor, cursor + duration, busy):
                slots.append(cursor)
            cursor += timedelta(minutes=settings.slot_step_minutes)
        return slots

    # ---------- запись ----------

    async def get_or_create_user(self, telegram_id: int, username: str | None) -> User:
        """Возвращает пользователя, создавая его при первом обращении."""
        user = await self._session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id, username=username)
            self._session.add(user)
            await self._session.flush()
        elif username and user.username != username:
            user.username = username
        return user

    async def create_booking(
        self,
        *,
        telegram_id: int,
        username: str | None,
        client_name: str,
        phone: str,
        service_id: int,
        master_id: int,
        starts_at: datetime,
        seats_count: int,
    ) -> Booking:
        """Создаёт бронирование с транзакционной защитой от гонок.

        Raises:
            SlotOccupiedError: слот уже занят.
            ValueError: услуга не найдена.
            SQLAlchemyError: любая другая ошибка СУБД (откат — снаружи).
        """
        service = await self.get_service(service_id)
        if service is None:
            raise ValueError(f"Service {service_id} not found")

        # Сериализуем конкурентные брони к этому мастеру до конца транзакции.
        await self._session.execute(select(func.pg_advisory_xact_lock(master_id)))

        duration = timedelta(minutes=service.duration_minutes)
        ends_at = starts_at + duration
        busy = await self._get_busy_intervals(master_id, starts_at, ends_at)
        if self._overlaps(starts_at, ends_at, busy):
            logger.warning("Slot conflict: master=%s starts_at=%s", master_id, starts_at)
            raise SlotOccupiedError

        user = await self.get_or_create_user(telegram_id, username)
        user.name = client_name
        user.phone = phone

        booking = Booking(
            user_id=user.id,
            service_id=service_id,
            master_id=master_id,
            starts_at=starts_at,
            ends_at=ends_at,
            seats_count=seats_count,
            status=BookingStatus.CONFIRMED,
        )
        self._session.add(booking)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # EXCLUDE-констрейнт: страховка, если конкурент обошёл advisory lock
            # (например, запись создана вне этого репозитория).
            logger.warning("Exclusion constraint hit: master=%s starts_at=%s", master_id, starts_at)
            raise SlotOccupiedError from exc

        logger.info(
            "Booking created: id=%s user=%s master=%s at=%s",
            booking.id,
            telegram_id,
            master_id,
            starts_at,
        )
        return booking

    async def cancel_booking(self, booking_id: int, telegram_id: int) -> Booking | None:
        """Отменяет запись, если она принадлежит пользователю и ещё активна.

        Returns:
            Отменённая запись или None (нет записи / чужая / уже неактивна).
        """
        booking = await self._session.scalar(
            select(Booking)
            .join(User, Booking.user_id == User.id)
            .where(
                Booking.id == booking_id,
                User.telegram_id == telegram_id,
                Booking.status.in_(ACTIVE_STATUSES),
            )
            .options(
                selectinload(Booking.service),
                selectinload(Booking.master),
                selectinload(Booking.user),
            )
            .with_for_update(of=Booking)
        )
        if booking is None:
            return None
        booking.status = BookingStatus.CANCELLED
        await self._session.flush()
        logger.info("Booking cancelled: id=%s user=%s", booking_id, telegram_id)
        return booking

    # ---------- внутреннее ----------

    async def _get_busy_intervals(
        self, master_id: int, window_start: datetime, window_end: datetime
    ) -> list[tuple[datetime, datetime]]:
        """Интервалы активных записей мастера, пересекающие окно [start, end)."""
        rows = await self._session.execute(
            select(Booking.starts_at, Booking.ends_at).where(
                Booking.master_id == master_id,
                Booking.status.in_(ACTIVE_STATUSES),
                Booking.starts_at < window_end,
                Booking.ends_at > window_start,
            )
        )
        return [(row.starts_at, row.ends_at) for row in rows.all()]

    @staticmethod
    def _overlaps(
        start: datetime,
        end: datetime,
        busy: list[tuple[datetime, datetime]],
    ) -> bool:
        return any(start < b_end and b_start < end for b_start, b_end in busy)
