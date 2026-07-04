"""Точка входа: запуск бота (long polling), диалогов и брокера Taskiq.

Запуск:
    python -m app.main                                            # бот
    taskiq worker app.tasks.broker:broker app.tasks.notifications # воркер задач
    taskiq scheduler app.tasks.broker:scheduler app.tasks.notifications # планировщик
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import DefaultKeyBuilder, RedisStorage
from aiogram.types import BotCommand
from aiogram_dialog import setup_dialogs

from app.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import create_engine, create_session_factory
from app.dialogs.booking import booking_dialog
from app.dialogs.booking import router as booking_router
from app.handlers.errors import router as errors_router
from app.handlers.my_bookings import router as my_bookings_router
from app.middlewares.db import DbSessionMiddleware
from app.tasks.broker import broker

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand(command="start", description="Записаться"),
    BotCommand(command="my", description="Мои записи"),
]


async def main() -> None:
    """Собирает зависимости и запускает polling."""
    settings = get_settings()
    setup_logging(settings.log_dir, settings.log_level)

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    # FSM-состояния в Redis: диалоги переживают рестарт бота.
    # with_destiny=True обязателен для aiogram-dialog.
    storage = RedisStorage.from_url(
        settings.redis_url,
        key_builder=DefaultKeyBuilder(with_destiny=True),
    )
    dp = Dispatcher(storage=storage)

    # Сессия БД на каждый update — без глобальных переменных.
    dp.update.outer_middleware(DbSessionMiddleware(session_factory))

    dp.include_router(errors_router)
    dp.include_router(booking_router)
    dp.include_router(my_bookings_router)
    dp.include_router(booking_dialog)
    setup_dialogs(dp)

    try:
        # Брокер Taskiq в режиме клиента (kicker) внутри процесса бота.
        await broker.startup()
        await bot.set_my_commands(BOT_COMMANDS)
        logger.info("Bot started")
        await dp.start_polling(bot)
    finally:
        await broker.shutdown()
        await engine.dispose()
        await bot.session.close()
        logger.info("Bot stopped")


if __name__ == "__main__":
    asyncio.run(main())
