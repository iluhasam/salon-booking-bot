"""Уведомления персоналу: администраторам из БД и мастеру записи.

Получатели определяются данными (роль ADMIN, связь мастер↔пользователь),
а не переменными окружения. Ошибки отправки не прерывают основной сценарий.
"""

from __future__ import annotations

import logging

from aiogram import Bot, html
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Booking
from app.db.users import UserRepository

logger = logging.getLogger(__name__)


async def notify_staff(bot: Bot, session: AsyncSession, master_id: int, text: str) -> None:
    """Шлёт `text` всем админам и мастеру записи (без дублей)."""
    repo = UserRepository(session)
    recipients = set(await repo.get_admin_telegram_ids())
    master_tg = await repo.get_master_telegram_id(master_id)
    if master_tg is not None:
        recipients.add(master_tg)

    if not recipients:
        logger.warning("No staff recipients: no admins in DB and master %s unlinked", master_id)
        return

    for chat_id in recipients:
        try:
            await bot.send_message(chat_id, text)
        except Exception:
            logger.exception("Failed to notify staff chat_id=%s", chat_id)


def booking_summary(booking: Booking, client_name: str, phone: str, master_name: str,
                    service_title: str) -> str:
    """Текст уведомления о записи (имя, услуга, дата, время, контакты)."""
    starts_local = booking.starts_at.astimezone(get_settings().tz)
    return (
        f"Клиент: {html.quote(client_name)} ({html.quote(phone)})\n"
        f"Услуга: {html.quote(service_title)}\n"
        f"Мастер: {html.quote(master_name)}\n"
        f"Когда: {starts_local:%d.%m.%Y %H:%M}\n"
        f"Мест: {booking.seats_count}"
    )
