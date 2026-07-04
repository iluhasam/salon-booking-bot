"""Конфигурация приложения (pydantic-settings, иммутабельный singleton)."""

from __future__ import annotations

from datetime import time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки, загружаемые из окружения / .env."""

    bot_token: str
    admin_chat_id: int

    database_url: str = "postgresql+asyncpg://bot:bot@localhost:5432/booking"
    redis_url: str = "redis://localhost:6379/0"

    log_dir: str = "logs"
    log_level: str = "INFO"

    # Часовой пояс салона: расписание и слоты считаются в нём.
    timezone: str = "Europe/Moscow"
    work_start: time = time(10, 0)
    work_end: time = time(20, 0)
    slot_step_minutes: int = 30
    reminder_offset_minutes: int = 120

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def reminder_offset(self) -> timedelta:
        return timedelta(minutes=self.reminder_offset_minutes)


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам."""
    return Settings()
