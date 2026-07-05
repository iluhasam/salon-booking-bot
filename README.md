# Salon Booking Bot

Асинхронный Telegram-бот записи клиентов салона.

**Стек:** Python 3.12 · aiogram 3.x + aiogram-dialog · PostgreSQL 16 (asyncpg) · SQLAlchemy 2.0 · Alembic · Taskiq + Redis · uv · Ruff · Docker Compose.

## Возможности

- Пошаговый диалог записи: услуга → количество мест → мастер → дата → время → имя/телефон → подтверждение.
- Свободные слоты считаются по индивидуальному графику мастера (часы по дням
  недели, отпуска/больничные) с учётом длительности услуги. График и отпуска
  редактируются в `/admin → Мастера → карточка мастера`.
- `/my` — просмотр и отмена своих записей.
- Напоминание клиенту за 2 часа до визита (настраивается), переживает рестарты.
- Валидация имени и телефона (Pydantic).

### Роли (хранятся в БД, без ID в конфигах)

| Роль | Возможности |
|---|---|
| **client** | запись, просмотр и отмена своих записей (`/start`, `/my`) |
| **master** | `/schedule` — своё расписание; уведомления о своих записях/отменах |
| **admin** | `/admin` — роли, графики и отпуска мастеров, блокировка, все записи; уведомления обо всём |

Добавление мастера без изменения кода: мастер запускает бота → админ в
`/admin → Пользователи` назначает ему роль «мастер» (профиль мастера
создаётся автоматически). Мастера не удаляются, а блокируются — история
записей сохраняется. Первый администратор назначается скриптом:

```bash
uv run python -m scripts.grant_admin <telegram_id>
# в Docker: docker compose run --rm bot python -m scripts.grant_admin <telegram_id>
```

## Структура проекта

```
app/
  config.py              # pydantic-settings: все настройки из окружения / .env
  main.py                # точка входа бота (long polling)
  core/logging.py        # консоль + ротируемые логи (bot.log, errors.log)
  db/
    models.py            # User (роль), Service, Master, графики, Booking
    engine.py            # асинхронный движок + фабрика сессий
    repository.py        # бронирования, слоты, защита от гонок
    users.py             # пользователи, роли, профили мастеров
    schedule.py          # графики по дням недели и отпуска
  dialogs/booking.py     # окна aiogram-dialog (сценарий записи)
  filters/role.py        # доступ по роли из БД
  handlers/
    my_bookings.py       # /my: список и отмена записей
    master.py            # /schedule: расписание мастера
    admin.py             # /admin: роли, графики, отпуска, записи
    errors.py            # глобальная обработка ошибок
  middlewares/
    db.py                # сессия БД на каждый update
    user.py              # авторизация: User с ролью в data['user']
  schemas/contact.py     # Pydantic-валидация имени/телефона
  services/notify.py     # уведомления персоналу (админы + мастер)
  tasks/
    broker.py            # Taskiq: брокер, планировщик, ресурсы воркера
    notifications.py     # напоминания (отложенные задачи, ретраи)
  utils/parse.py         # парсинг ввода админа (часы, интервалы дат)
migrations/              # Alembic (async env.py + версии 0001–0003)
scripts/
  seed.py                # идемпотентное наполнение справочников
  grant_admin.py         # назначение первого администратора
Dockerfile, docker-compose.yml, pyproject.toml, uv.lock
```

## Быстрый старт (Docker, рекомендуется)

Требуется Docker с Compose v2.

```bash
cp .env.example .env
# заполнить BOT_TOKEN (от @BotFather) и POSTGRES_PASSWORD

docker compose up -d --build       # postgres, redis, миграции, бот, воркер, планировщик
docker compose run --rm seed       # наполнить справочники услуг и мастеров (один раз)
docker compose run --rm bot python -m scripts.grant_admin <ваш telegram_id>  # первый админ
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
   `POSTGRES_PASSWORD`, реальный `BOT_TOKEN`).
3. `docker compose up -d --build && docker compose run --rm seed`, затем
   назначить первого администратора:
   `docker compose run --rm bot python -m scripts.grant_admin <telegram_id>`.
4. Данные PostgreSQL и Redis живут в именованных volume (`pg_data`,
   `redis_data`); все сервисы перезапускаются автоматически
   (`restart: unless-stopped`), миграции прогоняются при каждом старте.

Резервное копирование БД:

```bash
docker compose exec postgres pg_dump -U bot booking > backup_$(date +%F).sql
```
