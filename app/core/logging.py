"""Настройка логирования: консоль + ротируемые файлы (bot.log, errors.log)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logging(log_dir: str = "logs", level: str = "INFO") -> None:
    """Инициализирует корневой логгер.

    Пишет:
      * консоль    — все события уровня `level`+ (основной канал в Docker);
      * bot.log    — все события уровня `level`+;
      * errors.log — только ERROR+ (ошибки СУБД, отправки уведомлений).
    Файлы ротируются по 5 МБ, хранится 5 бэкапов.
    """
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(_FORMAT)
    root_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        path / "bot.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        path / "errors.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
    )
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.ERROR)

    root = logging.getLogger()
    root.setLevel(root_level)
    root.handlers.clear()
    root.addHandler(console)
    root.addHandler(file_handler)
    root.addHandler(error_handler)

    # Шумные логгеры сторонних библиотек не ниже WARNING.
    for noisy in ("aiogram.event", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
