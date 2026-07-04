"""Глобальная обработка ошибок диспетчера.

* UnknownIntent / UnknownState — пользователь нажал кнопку из «протухшего»
  диалога (например, после рестарта): вежливо предлагаем начать заново.
* Всё остальное — в лог (errors.log) c полным трейсбеком; пользователю
  нейтральное сообщение без технических деталей.
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import ExceptionTypeFilter
from aiogram.types import CallbackQuery, ErrorEvent
from aiogram_dialog.api.exceptions import UnknownIntent, UnknownState

logger = logging.getLogger(__name__)
router = Router(name="errors")

STALE_DIALOG_TEXT = "Эта кнопка устарела. Начните заново: /start"
GENERIC_ERROR_TEXT = "Что-то пошло не так. Попробуйте ещё раз: /start"


@router.errors(ExceptionTypeFilter(UnknownIntent, UnknownState))
async def on_stale_dialog(event: ErrorEvent) -> None:
    """Нажатие кнопки из завершённого/потерянного диалога."""
    logger.warning("Stale dialog interaction: %s", event.exception)
    callback = event.update.callback_query
    if isinstance(callback, CallbackQuery):
        await callback.answer(STALE_DIALOG_TEXT, show_alert=True)


@router.errors()
async def on_unhandled_error(event: ErrorEvent) -> None:
    """Последний рубеж: логируем и не даём упасть polling-циклу."""
    logger.exception("Unhandled error while processing update", exc_info=event.exception)
    callback = event.update.callback_query
    if isinstance(callback, CallbackQuery):
        try:
            await callback.answer(GENERIC_ERROR_TEXT, show_alert=True)
        except Exception:  # уведомление пользователя не критично
            logger.debug("Failed to notify user about error", exc_info=True)
