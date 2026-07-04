"""Просмотр и отмена своих записей: команда /my + inline-кнопки отмены."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from aiogram import Bot, F, Router, html
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Booking
from app.db.repository import BookingRepository

logger = logging.getLogger(__name__)
router = Router(name="my_bookings")

CANCEL_PREFIX = "cancel_booking:"


def _bookings_keyboard(bookings: Sequence[Booking]) -> InlineKeyboardMarkup:
    tz = get_settings().tz
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"❌ Отменить {b.starts_at.astimezone(tz):%d.%m %H:%M}",
                    callback_data=f"{CANCEL_PREFIX}{b.id}",
                )
            ]
            for b in bookings
        ]
    )


@router.message(Command("my"))
async def cmd_my_bookings(message: Message, session: AsyncSession) -> None:
    """Список будущих активных записей пользователя."""
    if message.from_user is None:
        return
    repo = BookingRepository(session)
    bookings = await repo.get_user_bookings(message.from_user.id)
    if not bookings:
        await message.answer("У вас нет активных записей. Записаться: /start")
        return

    tz = get_settings().tz
    lines = ["📋 Ваши записи:\n"]
    for b in bookings:
        lines.append(
            f"#{b.id} — {html.quote(b.service.title)}, мастер {html.quote(b.master.name)}\n"
            f"🗓 {b.starts_at.astimezone(tz):%d.%m.%Y %H:%M}, мест: {b.seats_count}\n"
        )
    await message.answer("\n".join(lines), reply_markup=_bookings_keyboard(bookings))


@router.callback_query(F.data.startswith(CANCEL_PREFIX))
async def on_cancel_booking(callback: CallbackQuery, session: AsyncSession, bot: Bot) -> None:
    """Отмена записи по кнопке (только своей и только активной)."""
    if callback.data is None:
        return
    try:
        booking_id = int(callback.data.removeprefix(CANCEL_PREFIX))
    except ValueError:
        await callback.answer("Некорректный запрос.", show_alert=True)
        return

    repo = BookingRepository(session)
    try:
        booking = await repo.cancel_booking(booking_id, callback.from_user.id)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while cancelling booking %s", booking_id)
        await callback.answer("Техническая ошибка, попробуйте позже.", show_alert=True)
        return

    if booking is None:
        await callback.answer("Запись не найдена или уже отменена.", show_alert=True)
        return

    settings = get_settings()
    starts_local = booking.starts_at.astimezone(settings.tz)
    await callback.answer("Запись отменена.")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"❌ Запись #{booking.id} на {starts_local:%d.%m.%Y %H:%M} отменена.\n"
            f"Записаться снова: /start"
        )

    # Уведомление администратору (не критично для клиента).
    try:
        await bot.send_message(
            settings.admin_chat_id,
            f"❌ Отмена записи #{booking.id} на {starts_local:%d.%m.%Y %H:%M}",
        )
    except Exception:
        logger.exception("Failed to notify admin about cancellation %s", booking.id)
