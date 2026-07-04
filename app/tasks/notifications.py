"""Фоновые задачи уведомлений (Taskiq + Redis).

* `send_booking_reminder` — напоминание клиенту; при сетевых ошибках
  Telegram API задача повторяется (SimpleRetryMiddleware, до 5 попыток).
* `schedule_reminder` — планирует напоминание за N минут до записи
  через RedisScheduleSource (переживает рестарт воркера).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from taskiq import Context, TaskiqDepends

from app.config import get_settings
from app.db.models import BookingStatus
from app.db.repository import BookingRepository
from app.tasks.broker import broker, schedule_source

logger = logging.getLogger(__name__)


@broker.task(retry_on_error=True, max_retries=5, delay=30)
async def send_booking_reminder(
    booking_id: int,
    telegram_id: int,
    context: Annotated[Context, TaskiqDepends()],
) -> None:
    """Отправляет клиенту напоминание о записи.

    Сетевые исключения пробрасываются наружу — middleware Taskiq
    перепланирует задачу (пауза задаётся label `delay`).
    """
    settings = get_settings()
    bot: Bot = context.state.bot

    async with context.state.session_factory() as session:
        booking = await BookingRepository(session).get_booking(booking_id)

    if booking is None or booking.status not in (
        BookingStatus.PENDING,
        BookingStatus.CONFIRMED,
    ):
        logger.info("Reminder skipped: booking %s missing/inactive", booking_id)
        return

    starts_local = booking.starts_at.astimezone(settings.tz)
    try:
        await bot.send_message(
            telegram_id,
            f"⏰ Напоминаем: сегодня в {starts_local:%H:%M} вы записаны в наш салон. Ждём вас!",
        )
        logger.info("Reminder sent: booking=%s user=%s", booking_id, telegram_id)
    except (TelegramNetworkError, TelegramRetryAfter):
        logger.exception("Network error sending reminder (booking=%s), will retry", booking_id)
        raise  # повторная попытка силами SimpleRetryMiddleware


async def schedule_reminder(booking_id: int, telegram_id: int, starts_at: datetime) -> None:
    """Планирует напоминание за `reminder_offset` до начала записи."""
    settings = get_settings()
    fire_at = (starts_at - settings.reminder_offset).astimezone(UTC)
    if fire_at <= datetime.now(UTC):
        logger.info("Booking %s is too soon, reminder not scheduled", booking_id)
        return

    await send_booking_reminder.schedule_by_time(schedule_source, fire_at, booking_id, telegram_id)
    logger.info("Reminder scheduled: booking=%s at %s", booking_id, fire_at)
