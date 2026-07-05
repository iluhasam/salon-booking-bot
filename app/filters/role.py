"""Фильтр доступа по роли пользователя (роль берётся из БД)."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.db.models import User, UserRole


class RoleFilter(BaseFilter):
    """Пропускает событие, только если роль пользователя входит в разрешённые.

    Модель User кладёт в data CurrentUserMiddleware.

    Пример:
        @router.message(Command("admin"), RoleFilter(UserRole.ADMIN))
    """

    def __init__(self, *roles: UserRole) -> None:
        self._roles = frozenset(roles)

    async def __call__(self, event: TelegramObject, user: User | None = None) -> bool:
        return user is not None and user.role in self._roles
