"""Модели данных SQLAlchemy 2.0 (Declarative, Mapped-типизация).

Все моменты времени хранятся как TIMESTAMPTZ (UTC внутри PostgreSQL),
отображение — в часовом поясе салона (settings.timezone).

Помимо индексов, в миграции 0001 создан EXCLUDE-констрейнт
`uq_bookings_no_overlap` (btree_gist): БД физически не допускает двух
пересекающихся активных записей к одному мастеру.
"""

from __future__ import annotations

import enum
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый декларативный класс."""


def _str_enum(enum_cls: type[enum.StrEnum]) -> Enum:
    """VARCHAR-колонка для StrEnum, хранящая значения ('client'), а не имена ('CLIENT').

    Значения совпадают с server_default в миграциях и с WHERE-условием
    EXCLUDE-констрейнта uq_bookings_no_overlap — имена бы его молча обходили.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=16,
        values_callable=lambda e: [member.value for member in e],
    )


class BookingStatus(enum.StrEnum):
    """Статусы бронирования."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class UserRole(enum.StrEnum):
    """Роль пользователя в системе.

    Роли хранятся в БД и назначаются администратором через /admin
    (первый админ — скриптом scripts/grant_admin.py). Никаких
    захардкоженных telegram_id в конфигурации.
    """

    CLIENT = "client"
    MASTER = "master"
    ADMIN = "admin"


class User(Base):
    """Пользователь бота (клиент, мастер или администратор)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(16))
    role: Mapped[UserRole] = mapped_column(
        _str_enum(UserRole),
        default=UserRole.CLIENT,
        server_default=UserRole.CLIENT.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bookings: Mapped[list[Booking]] = relationship(back_populates="user")
    master_profile: Mapped[Master | None] = relationship(back_populates="user")


class Service(Base):
    """Услуга салона."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    duration_minutes: Mapped[int]
    price: Mapped[int]

    bookings: Mapped[list[Booking]] = relationship(back_populates="service")


class Master(Base):
    """Мастер салона.

    Может быть связан с пользователем бота (user_id): тогда мастер
    получает уведомления о своих записях и видит расписание (/schedule).
    Мастеров не удаляют — деактивируют (is_active=False), чтобы история
    записей сохранялась.
    """

    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), unique=True
    )
    name: Mapped[str] = mapped_column(String(128))
    rating: Mapped[float] = mapped_column(default=5.0)
    is_active: Mapped[bool] = mapped_column(default=True, server_default="true")

    user: Mapped[User | None] = relationship(back_populates="master_profile")
    bookings: Mapped[list[Booking]] = relationship(back_populates="master")
    schedules: Mapped[list[MasterSchedule]] = relationship(
        back_populates="master", cascade="all, delete-orphan"
    )
    time_off: Mapped[list[MasterTimeOff]] = relationship(
        back_populates="master", cascade="all, delete-orphan"
    )


class MasterSchedule(Base):
    """Рабочие часы мастера в конкретный день недели.

    Наличие строки = мастер работает в этот день (weekday: 0=Пн … 6=Вс),
    отсутствие = выходной. При назначении роли MASTER создаётся график
    по умолчанию (часы салона, все дни).
    """

    __tablename__ = "master_schedules"
    __table_args__ = (UniqueConstraint("master_id", "weekday", name="uq_schedule_master_weekday"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    weekday: Mapped[int]  # 0 = понедельник … 6 = воскресенье
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)

    master: Mapped[Master] = relationship(back_populates="schedules")


class MasterTimeOff(Base):
    """Отпуск/недоступность мастера: интервал дат включительно.

    В эти дни клиенты не видят слотов мастера (пункт «мастер заболел /
    в отпуске» — без изменения графика).
    """

    __tablename__ = "master_time_off"
    __table_args__ = (Index("ix_time_off_master_dates", "master_id", "date_from", "date_to"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id", ondelete="CASCADE"))
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    reason: Mapped[str | None] = mapped_column(String(128))

    master: Mapped[Master] = relationship(back_populates="time_off")


class Booking(Base):
    """Запись клиента."""

    __tablename__ = "bookings"
    __table_args__ = (
        Index("ix_bookings_master_starts_at", "master_id", "starts_at"),
        Index("ix_bookings_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"))
    master_id: Mapped[int] = mapped_column(ForeignKey("masters.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    seats_count: Mapped[int] = mapped_column(default=1)
    status: Mapped[BookingStatus] = mapped_column(
        _str_enum(BookingStatus),
        default=BookingStatus.CONFIRMED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
    master: Mapped[Master] = relationship(back_populates="bookings")
