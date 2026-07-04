"""Middleware авторизации: находит/создаёт пользователя по Telegram ID.

Кладёт модель User в data['user'] — роль доступна фильтрам и хендлерам.
Регистрация нового пользователя коммитится сразу (иначе фильтры/хендлеры,
не делающие commit, потеряли бы её при закрытии сессии).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.users import UserRepository

logger = logging.getLogger(__name__)


class CurrentUserMiddleware(BaseMiddleware):
    """Автосоздание пользователя при первом обращении (см. ТЗ: авторизация)."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session: AsyncSession | None = data.get("session")
        tg_user = data.get("event_from_user")
        if session is not None and tg_user is not None and not tg_user.is_bot:
            try:
                user = await UserRepository(session).get_or_create(tg_user.id, tg_user.username)
                await session.commit()
                data["user"] = user
            except SQLAlchemyError:
                await session.rollback()
                logger.exception("Failed to load user telegram_id=%s", tg_user.id)
        return await handler(event, data)
