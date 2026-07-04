"""Фабрика подключений SQLAlchemy (PostgreSQL + asyncpg)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Создаёт асинхронный движок с проверкой соединений из пула."""
    return create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий; сессия живёт на время обработки одного update / задачи."""
    return async_sessionmaker(engine, expire_on_commit=False)
