"""Репозиторий пользователей и управления персоналом (роли, мастера)."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Master, User, UserRole

logger = logging.getLogger(__name__)


class UserRepository:
    """Работа с пользователями, ролями и профилями мастеров."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---------- пользователи ----------

    async def get_or_create(self, telegram_id: int, username: str | None) -> User:
        """Пользователь по telegram_id; создаётся при первом обращении."""
        user = await self._session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            user = User(telegram_id=telegram_id, username=username)
            self._session.add(user)
            await self._session.flush()
            logger.info("User registered: telegram_id=%s username=%s", telegram_id, username)
        elif username and user.username != username:
            user.username = username
        return user

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def list_recent(self, limit: int = 20) -> list[User]:
        """Последние зарегистрировавшиеся пользователи (для админ-меню)."""
        result = await self._session.scalars(
            select(User).order_by(User.created_at.desc()).limit(limit)
        )
        return list(result)

    async def get_admin_telegram_ids(self) -> list[int]:
        """Telegram ID всех администраторов (получатели уведомлений)."""
        result = await self._session.scalars(
            select(User.telegram_id).where(User.role == UserRole.ADMIN)
        )
        return list(result)

    # ---------- роли ----------

    async def set_role(self, user_id: int, role: UserRole) -> User | None:
        """Назначает роль и синхронизирует профиль мастера.

        MASTER: создаёт профиль мастера с графиком по умолчанию
        (или реактивирует существующий). Понижение с MASTER: деактивирует
        профиль (история записей остаётся).
        """
        user = await self.get_by_id(user_id)
        if user is None:
            return None

        old_role = user.role
        user.role = role

        master = await self._session.scalar(select(Master).where(Master.user_id == user_id))
        if role == UserRole.MASTER:
            if master is None:
                display_name = user.name or user.username or f"Мастер #{user.id}"
                master = Master(user_id=user.id, name=display_name)
                self._session.add(master)
                await self._session.flush()
                # График по умолчанию (часы салона), чтобы мастер сразу был доступен.
                from app.db.schedule import ScheduleRepository

                await ScheduleRepository(self._session).create_default_week(master.id)
            else:
                master.is_active = True
        elif old_role == UserRole.MASTER and master is not None:
            master.is_active = False

        await self._session.flush()
        logger.info("Role changed: user=%s %s -> %s", user_id, old_role, role)
        return user

    # ---------- мастера ----------

    async def get_master_by_user(self, user_id: int) -> Master | None:
        return await self._session.scalar(select(Master).where(Master.user_id == user_id))

    async def list_masters(self, include_inactive: bool = True) -> list[Master]:
        stmt = select(Master).order_by(Master.name)
        if not include_inactive:
            stmt = stmt.where(Master.is_active.is_(True))
        return list(await self._session.scalars(stmt))

    async def toggle_master(self, master_id: int) -> Master | None:
        """Блокировка/разблокировка мастера (без удаления)."""
        master = await self._session.get(Master, master_id)
        if master is None:
            return None
        master.is_active = not master.is_active
        await self._session.flush()
        logger.info("Master %s is_active=%s", master_id, master.is_active)
        return master

    async def get_master_telegram_id(self, master_id: int) -> int | None:
        """Telegram ID пользователя, привязанного к мастеру (если есть)."""
        return await self._session.scalar(
            select(User.telegram_id)
            .join(Master, Master.user_id == User.id)
            .where(Master.id == master_id)
        )
