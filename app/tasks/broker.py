"""Брокер Taskiq на Redis + источник отложенных задач.

Воркер:      taskiq worker app.tasks.broker:broker app.tasks.notifications
Планировщик: taskiq scheduler app.tasks.broker:scheduler app.tasks.notifications

Ресурсы воркера (движок БД, Bot) создаются один раз на процесс в событии
WORKER_STARTUP и доступны задачам через Context.state — без пересоздания
подключений на каждую задачу.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.middlewares import SimpleRetryMiddleware
from taskiq_redis import ListQueueBroker, ListRedisScheduleSource

from app.config import get_settings
from app.core.logging import setup_logging
from app.db.engine import create_engine, create_session_factory

_settings = get_settings()

# socket_timeout=None обязателен: redis-py 8.x по умолчанию ставит read-timeout
# на сокет, и бесконечный BRPOP воркера обрывается по TimeoutError.
# SimpleRetryMiddleware повторяет задачу при исключении (retry_on_error в labels).
broker = ListQueueBroker(_settings.redis_url, socket_timeout=None).with_middlewares(
    SimpleRetryMiddleware(default_retry_count=5),
)

# Источник расписаний в Redis — переживает рестарты процессов.
schedule_source = ListRedisScheduleSource(_settings.redis_url, socket_timeout=None)

scheduler = TaskiqScheduler(broker=broker, sources=[schedule_source])


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def on_worker_startup(state: TaskiqState) -> None:
    """Инициализация ресурсов процесса-воркера."""
    setup_logging(_settings.log_dir, _settings.log_level)
    state.engine = create_engine(_settings.database_url)
    state.session_factory = create_session_factory(state.engine)
    state.bot = Bot(
        token=_settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def on_worker_shutdown(state: TaskiqState) -> None:
    """Освобождение ресурсов процесса-воркера."""
    await state.bot.session.close()
    await state.engine.dispose()
