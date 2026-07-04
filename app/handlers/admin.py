"""Админ-меню (/admin): пользователи, роли, мастера, ближайшие записи.

Доступ — только для роли ADMIN (RoleFilter, роль из БД). Управление
персоналом не требует изменения кода или .env: мастер запускает бота,
администратор назначает ему роль MASTER из списка пользователей.
"""

from __future__ import annotations

import logging

from aiogram import F, Router, html
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import User, UserRole
from app.db.repository import BookingRepository
from app.db.users import UserRepository
from app.filters.role import RoleFilter

logger = logging.getLogger(__name__)
router = Router(name="admin")
router.message.filter(RoleFilter(UserRole.ADMIN))
router.callback_query.filter(RoleFilter(UserRole.ADMIN))

ROLE_LABELS = {
    UserRole.CLIENT: "клиент",
    UserRole.MASTER: "мастер",
    UserRole.ADMIN: "админ",
}
DB_ERROR_TEXT = "Техническая ошибка, попробуйте позже."


def _menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Пользователи", callback_data="adm:users")],
            [InlineKeyboardButton(text="💇 Мастера", callback_data="adm:masters")],
            [InlineKeyboardButton(text="📅 Ближайшие записи", callback_data="adm:bookings")],
        ]
    )


def _user_label(user: User) -> str:
    name = user.name or user.username or f"id{user.telegram_id}"
    return f"{name} · {ROLE_LABELS[user.role]}"


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    """Точка входа в админ-меню."""
    await message.answer("🛠 Админ-меню:", reply_markup=_menu_keyboard())


@router.callback_query(F.data == "adm:menu")
async def cb_menu(callback: CallbackQuery) -> None:
    if isinstance(callback.message, Message):
        await callback.message.edit_text("🛠 Админ-меню:", reply_markup=_menu_keyboard())
    await callback.answer()


# ---------------------------------------------------------------- users


@router.callback_query(F.data == "adm:users")
async def cb_users(callback: CallbackQuery, session: AsyncSession) -> None:
    """Последние зарегистрировавшиеся пользователи."""
    users = await UserRepository(session).list_recent(limit=20)
    keyboard = [
        [InlineKeyboardButton(text=_user_label(u), callback_data=f"adm:user:{u.id}")]
        for u in users
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="adm:menu")])
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "👥 Пользователи (последние 20).\nНажмите, чтобы изменить роль:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def cb_user_card(callback: CallbackQuery, session: AsyncSession) -> None:
    """Карточка пользователя с выбором роли."""
    user_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    target = await UserRepository(session).get_by_id(user_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    role_buttons = [
        InlineKeyboardButton(
            text=("✅ " if target.role == role else "") + label.capitalize(),
            callback_data=f"adm:role:{user_id}:{role.value}",
        )
        for role, label in ROLE_LABELS.items()
    ]
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"👤 {html.quote(target.name or '—')} (@{target.username or '—'})\n"
            f"Telegram ID: <code>{target.telegram_id}</code>\n"
            f"Телефон: {html.quote(target.phone or '—')}\n"
            f"Роль: <b>{ROLE_LABELS[target.role]}</b>",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    role_buttons,
                    [InlineKeyboardButton(text="⬅️ К списку", callback_data="adm:users")],
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:role:"))
async def cb_set_role(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    """Назначение роли (с защитой от снятия последнего админа)."""
    _, _, raw_id, raw_role = callback.data.split(":")  # type: ignore[union-attr]
    user_id, new_role = int(raw_id), UserRole(raw_role)

    repo = UserRepository(session)
    target = await repo.get_by_id(user_id)
    if target is None:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return
    if (
        target.role == UserRole.ADMIN
        and new_role != UserRole.ADMIN
        and len(await repo.get_admin_telegram_ids()) <= 1
    ):
        await callback.answer("Нельзя снять роль у последнего администратора.", show_alert=True)
        return

    try:
        await repo.set_role(user_id, new_role)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while setting role user=%s", user_id)
        await callback.answer(DB_ERROR_TEXT, show_alert=True)
        return

    logger.info("Admin %s set role %s for user %s", user.telegram_id, new_role, user_id)
    await callback.answer(f"Роль обновлена: {ROLE_LABELS[new_role]}")
    await cb_user_card(callback, session)


# ---------------------------------------------------------------- masters


@router.callback_query(F.data == "adm:masters")
async def cb_masters(callback: CallbackQuery, session: AsyncSession) -> None:
    """Список мастеров с блокировкой/разблокировкой."""
    masters = await UserRepository(session).list_masters(include_inactive=True)
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if m.is_active else '⛔'} {m.name} ★{m.rating:.1f}",
                callback_data=f"adm:mtoggle:{m.id}",
            )
        ]
        for m in masters
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="adm:menu")])
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "💇 Мастера (нажмите для блокировки/разблокировки).\n"
            "Заблокированный мастер скрыт из записи, история сохраняется.\n"
            "Добавить мастера: Пользователи → выбрать → роль «Мастер».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:mtoggle:"))
async def cb_toggle_master(callback: CallbackQuery, session: AsyncSession, user: User) -> None:
    master_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    try:
        master = await UserRepository(session).toggle_master(master_id)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while toggling master %s", master_id)
        await callback.answer(DB_ERROR_TEXT, show_alert=True)
        return

    if master is None:
        await callback.answer("Мастер не найден.", show_alert=True)
        return
    logger.info("Admin %s toggled master %s -> %s", user.telegram_id, master_id, master.is_active)
    await callback.answer("Мастер разблокирован." if master.is_active else "Мастер заблокирован.")
    await cb_masters(callback, session)


# ---------------------------------------------------------------- bookings


@router.callback_query(F.data == "adm:bookings")
async def cb_bookings(callback: CallbackQuery, session: AsyncSession) -> None:
    """Ближайшие активные записи всех мастеров."""
    bookings = await BookingRepository(session).get_upcoming_bookings(limit=15)
    tz = get_settings().tz
    if not bookings:
        text = "📅 Активных записей нет."
    else:
        lines = ["📅 Ближайшие записи:\n"]
        for b in bookings:
            client = b.user.name or b.user.username or f"id{b.user.telegram_id}"
            lines.append(
                f"#{b.id} {b.starts_at.astimezone(tz):%d.%m %H:%M} — "
                f"{html.quote(b.service.title)}, {html.quote(b.master.name)}, "
                f"{html.quote(client)}"
            )
        text = "\n".join(lines)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Меню", callback_data="adm:menu")]]
            ),
        )
    await callback.answer()
