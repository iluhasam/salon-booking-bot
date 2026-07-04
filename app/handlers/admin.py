"""Админ-меню (/admin): пользователи, роли, мастера, графики, записи.

Доступ — только для роли ADMIN (RoleFilter, роль из БД). Управление
персоналом не требует изменения кода или .env: мастер запускает бота,
администратор назначает ему роль MASTER из списка пользователей.

Карточка мастера: рабочие часы по дням недели (FSM-ввод «10:00-19:00» /
«выходной») и отпуска («15.07-20.07»), блокировка без удаления.
"""

from __future__ import annotations

import logging
from datetime import date

from aiogram import F, Router, html
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Master, User, UserRole
from app.db.repository import BookingRepository
from app.db.schedule import WEEKDAYS_RU, ScheduleRepository
from app.db.users import UserRepository
from app.filters.role import RoleFilter
from app.utils.parse import is_day_off, parse_date_range, parse_hours

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


class AdminInput(StatesGroup):
    """FSM-состояния текстового ввода админа."""

    day_hours = State()
    time_off = State()


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
async def cmd_admin(message: Message, state: FSMContext) -> None:
    """Точка входа в админ-меню."""
    await state.clear()
    await message.answer("🛠 Админ-меню:", reply_markup=_menu_keyboard())


@router.callback_query(F.data == "adm:menu")
async def cb_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
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


async def _master_card(
    session: AsyncSession, master: Master
) -> tuple[str, InlineKeyboardMarkup]:
    """Текст и клавиатура карточки мастера (график + отпуска)."""
    schedule_repo = ScheduleRepository(session)
    week = await schedule_repo.get_week(master.id)
    time_off = await schedule_repo.list_time_off(master.id, date.today())

    lines = [
        f"💇 <b>{html.quote(master.name)}</b> ★{master.rating:.1f} — "
        f"{'🟢 активен' if master.is_active else '⛔ заблокирован'}",
        "",
        "🗓 График (нажмите день, чтобы изменить):",
    ]
    for wd in range(7):
        hours = week.get(wd)
        label = f"{hours[0]:%H:%M}–{hours[1]:%H:%M}" if hours else "выходной"
        lines.append(f"  {WEEKDAYS_RU[wd]}: {label}")

    if time_off:
        lines.append("")
        lines.append("🏖 Недоступен (нажмите, чтобы удалить):")

    day_buttons = [
        InlineKeyboardButton(
            text=f"{WEEKDAYS_RU[wd]}{'' if wd in week else ' ✖'}",
            callback_data=f"adm:mday:{master.id}:{wd}",
        )
        for wd in range(7)
    ]
    keyboard: list[list[InlineKeyboardButton]] = [day_buttons[:4], day_buttons[4:]]
    keyboard.extend(
        [
            InlineKeyboardButton(
                text=f"🗑 {t.date_from:%d.%m}–{t.date_to:%d.%m}",
                callback_data=f"adm:tdel:{master.id}:{t.id}",
            )
        ]
        for t in time_off
    )
    keyboard.append(
        [
            InlineKeyboardButton(text="➕ Отпуск", callback_data=f"adm:tadd:{master.id}"),
            InlineKeyboardButton(
                text="⛔ Заблокировать" if master.is_active else "🟢 Разблокировать",
                callback_data=f"adm:mtoggle:{master.id}",
            ),
        ]
    )
    keyboard.append([InlineKeyboardButton(text="⬅️ К мастерам", callback_data="adm:masters")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _show_master_card(
    callback: CallbackQuery, session: AsyncSession, master_id: int
) -> None:
    master = await BookingRepository(session).get_master(master_id)
    if master is None:
        await callback.answer("Мастер не найден.", show_alert=True)
        return
    text, keyboard = await _master_card(session, master)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data == "adm:masters")
async def cb_masters(callback: CallbackQuery, session: AsyncSession, state: FSMContext) -> None:
    """Список мастеров; карточка — по нажатию."""
    await state.clear()
    masters = await UserRepository(session).list_masters(include_inactive=True)
    keyboard = [
        [
            InlineKeyboardButton(
                text=f"{'🟢' if m.is_active else '⛔'} {m.name} ★{m.rating:.1f}",
                callback_data=f"adm:mcard:{m.id}",
            )
        ]
        for m in masters
    ]
    keyboard.append([InlineKeyboardButton(text="⬅️ Меню", callback_data="adm:menu")])
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "💇 Мастера (нажмите для графика и настроек).\n"
            "Добавить мастера: Пользователи → выбрать → роль «Мастер».",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:mcard:"))
async def cb_master_card(callback: CallbackQuery, session: AsyncSession) -> None:
    master_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    await _show_master_card(callback, session, master_id)


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
    await _show_master_card(callback, session, master_id)


# ---------------------------------------------------------------- schedule input


@router.callback_query(F.data.startswith("adm:mday:"))
async def cb_edit_day(callback: CallbackQuery, state: FSMContext) -> None:
    """Запрашивает часы для дня недели."""
    _, _, raw_master, raw_wd = callback.data.split(":")  # type: ignore[union-attr]
    await state.set_state(AdminInput.day_hours)
    await state.update_data(master_id=int(raw_master), weekday=int(raw_wd))
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Часы работы в <b>{WEEKDAYS_RU[int(raw_wd)]}</b>?\n"
            f"Например: <code>10:00-19:00</code> — или напишите «выходной».",
        )
    await callback.answer()


@router.message(StateFilter(AdminInput.day_hours), F.text)
async def msg_day_hours(message: Message, session: AsyncSession, state: FSMContext) -> None:
    """Применяет введённые часы / выходной."""
    data = await state.get_data()
    master_id, weekday = data["master_id"], data["weekday"]
    text = message.text or ""

    repo = ScheduleRepository(session)
    try:
        if is_day_off(text):
            await repo.clear_day(master_id, weekday)
            result = f"{WEEKDAYS_RU[weekday]} — выходной."
        else:
            hours = parse_hours(text)
            if hours is None:
                await message.answer(
                    "Не понял. Формат: <code>10:00-19:00</code> или «выходной»."
                )
                return
            await repo.set_day(master_id, weekday, *hours)
            result = f"{WEEKDAYS_RU[weekday]}: {hours[0]:%H:%M}–{hours[1]:%H:%M}."
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while editing schedule master=%s", master_id)
        await message.answer(DB_ERROR_TEXT)
        return

    await state.clear()
    master = await BookingRepository(session).get_master(master_id)
    if master is not None:
        text_card, keyboard = await _master_card(session, master)
        await message.answer(f"✅ {result}\n\n{text_card}", reply_markup=keyboard)


# ---------------------------------------------------------------- time off


@router.callback_query(F.data.startswith("adm:tadd:"))
async def cb_add_time_off(callback: CallbackQuery, state: FSMContext) -> None:
    master_id = int(callback.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    await state.set_state(AdminInput.time_off)
    await state.update_data(master_id=master_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Даты недоступности (включительно)?\n"
            "Например: <code>15.07-20.07</code> — или одна дата <code>15.07</code>.",
        )
    await callback.answer()


@router.message(StateFilter(AdminInput.time_off), F.text)
async def msg_time_off(message: Message, session: AsyncSession, state: FSMContext) -> None:
    data = await state.get_data()
    master_id = data["master_id"]

    period = parse_date_range(message.text or "", date.today())
    if period is None:
        await message.answer("Не понял. Формат: <code>15.07-20.07</code> или <code>15.07</code>.")
        return

    try:
        await ScheduleRepository(session).add_time_off(master_id, *period)
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while adding time off master=%s", master_id)
        await message.answer(DB_ERROR_TEXT)
        return

    await state.clear()
    master = await BookingRepository(session).get_master(master_id)
    if master is not None:
        text_card, keyboard = await _master_card(session, master)
        await message.answer(
            f"✅ Недоступен {period[0]:%d.%m.%Y}–{period[1]:%d.%m.%Y}.\n\n{text_card}",
            reply_markup=keyboard,
        )


@router.callback_query(F.data.startswith("adm:tdel:"))
async def cb_del_time_off(callback: CallbackQuery, session: AsyncSession) -> None:
    _, _, raw_master, raw_id = callback.data.split(":")  # type: ignore[union-attr]
    try:
        deleted = await ScheduleRepository(session).delete_time_off(int(raw_id))
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while deleting time off %s", raw_id)
        await callback.answer(DB_ERROR_TEXT, show_alert=True)
        return
    if not deleted:
        await callback.answer("Интервал не найден.", show_alert=True)
        return
    await _show_master_card(callback, session, int(raw_master))


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
