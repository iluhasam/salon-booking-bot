"""Модели данных SQLAlchemy 2.0 (Declarative, Mapped-типизация).

Все моменты времени хранятся как TIMESTAMPTZ (UTC внутри PostgreSQL),
отображение — в часовом поясе салона (settings.timezone).

Помимо индексов, в миграции 0001 создан EXCLUDE-констрейнт
`uq_bookings_no_overlap` (btree_gist): БД физически не допускает двух
пересекающихся активных записей к одному мастеру.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый декларативный класс."""


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
        Enum(UserRole, native_enum=False, length=16),
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
        Enum(BookingStatus, native_enum=False, length=16),
        default=BookingStatus.CONFIRMED,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="bookings")
    service: Mapped[Service] = relationship(back_populates="bookings")
    master: Mapped[Master] = relationship(back_populates="bookings")
