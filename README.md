# Salon Booking Bot

Асинхронный Telegram-бот записи клиентов салона.

**Стек:** Python 3.12 · aiogram 3.x + aiogram-dialog · PostgreSQL 16 (asyncpg) · SQLAlchemy 2.0 · Alembic · Taskiq + Redis · uv · Ruff · Docker Compose.

## Возможности

- Пошаговый диалог записи: услуга → количество мест → мастер → дата → время → имя/телефон → подтверждение.
- Свободные слоты считаются по рабочим часам салона с учётом длительности услуги.
- `/my` — просмотр и отмена своих записей.
- Напоминание клиенту за 2 часа до визита (настраивается), переживает рестарты.
- Уведомления администратору о новых записях и отменах.
- Валидация имени и телефона (Pydantic).

## Структура проекта

```
app/
  config.py              # pydantic-settings: все настройки из окружения / .env
  main.py                # точка входа бота (long polling)
  core/logging.py        # консоль + ротируемые логи (bot.log, errors.log)
  db/
    models.py            # User, Service, Master, Booking (SQLAlchemy 2.0)
    engine.py            # асинхронный движок + фабрика сессий
    repository.py        # бизнес-логика, защита слотов от гонок
  dialogs/booking.py     # окна aiogram-dialog (сценарий записи)
  handlers/
    my_bookings.py       # /my: список и отмена записей
    errors.py            # глобальная обработка ошибок
  middlewares/db.py      # сессия БД на каждый update
  schemas/contact.py     # Pydantic-валидация имени/телефона
  tasks/
    broker.py            # Taskiq: брокер, планировщик, ресурсы воркера
    notifications.py     # напоминания (отложенные задачи, ретраи)
migrations/              # Alembic (async env.py + версии)
scripts/seed.py          # идемпотентное наполнение справочников
Dockerfile, docker-compose.yml, pyproject.toml, uv.lock
```

## Быстрый старт (Docker, рекомендуется)

Требуется Docker с Compose v2.

```bash
cp .env.example .env
# заполнить BOT_TOKEN (от @BotFather), ADMIN_CHAT_ID и POSTGRES_PASSWORD

docker compose up -d --build       # postgres, redis, миграции, бот, воркер, планировщик
docker compose run --rm seed       # наполнить справочники услуг и мастеров (один раз)
docker compose logs -f bot         # логи бота
```

Миграции применяются автоматически сервисом `migrate` перед стартом бота.

## Локальная разработка (uv)

```bash
uv sync                                  # окружение + зависимости (включая dev)
cp .env.example .env                     # заполнить и поднять локальные PostgreSQL/Redis
docker compose up -d postgres redis      # либо свои инстансы

uv run alembic upgrade head              # миграции
uv run python -m scripts.seed            # справочники
uv run python -m app.main                # бот
uv run taskiq worker app.tasks.broker:broker app.tasks.notifications        # воркер
uv run taskiq scheduler app.tasks.broker:scheduler app.tasks.notifications  # планировщик
```

Линтер и форматирование:

```bash
uv run ruff check .
uv run ruff format .
```

## Конфигурация

Все настройки — через переменные окружения (см. `.env.example`):

| Переменная | Описание | По умолчанию |
|---|---|---|
| `BOT_TOKEN` | токен бота Telegram | — (обязательна) |
| `ADMIN_CHAT_ID` | чат для уведомлений администратора | — (обязательна) |
| `DATABASE_URL` | PostgreSQL DSN (`postgresql+asyncpg://…`) | localhost |
| `REDIS_URL` | Redis (очередь задач + FSM-состояния) | localhost |
| `TIMEZONE` | часовой пояс салона | `Europe/Moscow` |
| `WORK_START` / `WORK_END` | рабочие часы | `10:00` / `20:00` |
| `SLOT_STEP_MINUTES` | шаг сетки слотов | `30` |
| `REMINDER_OFFSET_MINUTES` | за сколько минут напоминать | `120` |
| `LOG_LEVEL` | уровень логирования | `INFO` |

## Миграции

```bash
uv run alembic revision --autogenerate -m "описание"   # новая миграция
uv run alembic upgrade head                             # применить
# в Docker: docker compose run --rm migrate
```

## Ключевые архитектурные решения

- **Гонки за слот — двухуровневая защита.** В транзакции берётся
  `pg_advisory_xact_lock(master_id)`: проверка пересечений и `INSERT`
  сериализованы по мастеру. Страховка на уровне БД — EXCLUDE-констрейнт
  (btree_gist) на пересечение `tstzrange(starts_at, ends_at)` активных записей:
  двойное бронирование физически невозможно. Пользователь при конфликте
  возвращается к выбору времени.
- **Время.** В БД — `timestamptz` (UTC); расписание и отображение — в часовом
  поясе салона (`TIMEZONE`).
- **Напоминания.** Хранятся в Redis (`ListRedisScheduleSource`), переживают
  рестарты; при сетевых ошибках Telegram — до 5 ретраев с паузой 30 с
  (`SimpleRetryMiddleware`). Отменённые записи пропускаются.
- **FSM-состояния в Redis** — диалоги переживают рестарт бота; «протухшие»
  кнопки обрабатываются глобальным error-хендлером.
- **Ошибки СУБД:** rollback + запись в `errors.log`; пользователю — нейтральное
  сообщение без деталей.
- **Ресурсы воркера** (движок БД, Bot) создаются один раз на процесс
  (`WORKER_STARTUP`), а не на задачу.

## Развертывание на VPS

1. Установить Docker + Compose v2.
2. Склонировать проект, создать `.env` из `.env.example` (сильный
   `POSTGRES_PASSWORD`, реальные `BOT_TOKEN` и `ADMIN_CHAT_ID`).
3. `docker compose up -d --build && docker compose run --rm seed`.
4. Данные PostgreSQL и Redis живут в именованных volume (`pg_data`,
   `redis_data`); все сервисы перезапускаются автоматически
   (`restart: unless-stopped`), миграции прогоняются при каждом старте.

Резервное копирование БД:

```bash
docker compose exec postgres pg_dump -U bot booking > backup_$(date +%F).sql
```
