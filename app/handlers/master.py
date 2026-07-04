"""Кабинет мастера: /schedule — своё расписание (роль MASTER или ADMIN)."""

from __future__ import annotations

import logging

from aiogram import Router, html
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User, UserRole
from app.db.repository import BookingRepository
from app.db.users import UserRepository
from app.filters.role import RoleFilter

logger = logging.getLogger(__name__)
router = Router(name="master")


@router.message(Command("schedule"), RoleFilter(UserRole.MASTER, UserRole.ADMIN))
async def cmd_schedule(message: Message, session: AsyncSession, user: User) -> None:
    """Будущие записи мастера, привязанного к текущему пользователю."""
    master = await UserRepository(session).get_master_by_user(user.id)
    if master is None:
        await message.answer(
            "К вашему аккаунту не привязан профиль мастера. Обратитесь к администратору."
        )
        return

    bookings = await BookingRepository(session).get_upcoming_bookings(master_id=master.id)
    if not bookings:
        await message.answer("📅 У вас нет предстоящих записей.")
        return

    tz = get_settings().tz
    lines = [f"📅 Ваше расписание, {html.quote(master.name)}:\n"]
    for b in bookings:
        client = b.user.name or b.user.username or f"id{b.user.telegram_id}"
        phone = b.user.phone or "—"
        lines.append(
            f"#{b.id} {b.starts_at.astimezone(tz):%d.%m %H:%M} — "
            f"{html.quote(b.service.title)}\n"
            f"    {html.quote(client)}, {html.quote(phone)}, мест: {b.seats_count}"
        )
    await message.answer("\n".join(lines))
