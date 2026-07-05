"""Назначение первого администратора (bootstrap, без хардкода в .env).

Использование:
    uv run python -m scripts.grant_admin <telegram_id>
    # в Docker: docker compose run --rm bot python -m scripts.grant_admin <telegram_id>

Дальнейшее управление ролями — через /admin в боте.
"""

from __future__ import annotations

import asyncio
import sys

from app.config import get_settings
from app.db.engine import create_engine, create_session_factory
from app.db.models import UserRole
from app.db.users import UserRepository


async def grant(telegram_id: int) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            repo = UserRepository(session)
            user = await repo.get_or_create(telegram_id, username=None)
            await repo.set_role(user.id, UserRole.ADMIN)
            await session.commit()
            print(f"OK: user telegram_id={telegram_id} is now ADMIN (user_id={user.id})")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].lstrip("-").isdigit():
        print("Usage: python -m scripts.grant_admin <telegram_id>")
        raise SystemExit(1)
    asyncio.run(grant(int(sys.argv[1])))
