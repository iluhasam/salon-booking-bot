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


class User(Base):
    """Клиент салона."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    name: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    bookings: Mapped[list[Booking]] = relationship(back_populates="user")


class Service(Base):
    """Услуга салона."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    duration_minutes: Mapped[int]
    price: Mapped[int]

    bookings: Mapped[list[Booking]] = relationship(back_populates="service")


class Master(Base):
    """Мастер."""

    __tablename__ = "masters"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    rating: Mapped[float] = mapped_column(default=5.0)

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
