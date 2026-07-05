"""Идемпотентное наполнение справочников (услуги, мастера).

Запуск: python -m scripts.seed
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import create_engine, create_session_factory
from app.db.models import Master, Service
from app.db.schedule import ScheduleRepository

logger = logging.getLogger(__name__)

SERVICES = [
    {"title": "Стрижка", "duration_minutes": 60, "price": 1500},
    {"title": "Окрашивание", "duration_minutes": 120, "price": 4000},
    {"title": "Маникюр", "duration_minutes": 90, "price": 2000},
    {"title": "Укладка", "duration_minutes": 45, "price": 1200},
]

MASTERS = [
    {"name": "Анна", "rating": 4.9},
    {"name": "Мария", "rating": 4.7},
    {"name": "Ольга", "rating": 4.8},
]


async def seed() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            existing_services = set(await session.scalars(select(Service.title)))
            existing_masters = set(await session.scalars(select(Master.name)))

            added = 0
            for data in SERVICES:
                if data["title"] not in existing_services:
                    session.add(Service(**data))
                    added += 1

            schedule_repo = ScheduleRepository(session)
            for data in MASTERS:
                if data["name"] not in existing_masters:
                    master = Master(**data)
                    session.add(master)
                    await session.flush()
                    # График по умолчанию (часы салона, все дни).
                    await schedule_repo.create_default_week(master.id)
                    added += 1

            await session.commit()
            logger.info("Seed complete: %s new rows", added)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    setup_logging(get_settings().log_dir, get_settings().log_level)
    asyncio.run(seed())
