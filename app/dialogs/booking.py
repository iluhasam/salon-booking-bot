"""Сценарий бронирования на aiogram-dialog.

Окна (Window) описывают только отображение; вся работа с БД — в геттерах
и коллбеках через BookingRepository. Сессия SQLAlchemy приходит из
DbSessionMiddleware, глобальных состояний нет.
"""

from __future__ import annotations

import logging
import operator
from datetime import date, datetime
from typing import Any

from aiogram import Bot, F, Router, html
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram_dialog import Dialog, DialogManager, StartMode, Window
from aiogram_dialog.widgets.input import ManagedTextInput, TextInput
from aiogram_dialog.widgets.kbd import (
    Back,
    Button,
    Calendar,
    Cancel,
    Column,
    Group,
    Select,
)
from aiogram_dialog.widgets.text import Const, Format
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.repository import BookingRepository, SlotOccupiedError
from app.schemas.contact import ContactSchema
from app.tasks.notifications import schedule_reminder

logger = logging.getLogger(__name__)
router = Router(name="booking")

DB_ERROR_TEXT = (
    "К сожалению, не удалось сохранить запись из-за технической ошибки. "
    "Попробуйте ещё раз чуть позже."
)


class BookingSG(StatesGroup):
    """Состояния диалога бронирования."""

    service = State()
    seats = State()
    master = State()
    calendar = State()
    time_slot = State()
    name = State()
    phone = State()
    confirm = State()


# ---------------------------------------------------------------- getters


async def services_getter(
    dialog_manager: DialogManager, session: AsyncSession, **_: Any
) -> dict[str, Any]:
    """Список услуг из БД."""
    repo = BookingRepository(session)
    services = await repo.get_services()
    return {
        "services": [
            (s.id, f"{s.title} — {s.price} ₽ ({s.duration_minutes} мин)") for s in services
        ]
    }


async def masters_getter(
    dialog_manager: DialogManager, session: AsyncSession, **_: Any
) -> dict[str, Any]:
    """Список мастеров из БД."""
    repo = BookingRepository(session)
    masters = await repo.get_masters()
    return {"masters": [(m.id, f"{m.name} ★{m.rating:.1f}") for m in masters]}


async def slots_getter(
    dialog_manager: DialogManager, session: AsyncSession, **_: Any
) -> dict[str, Any]:
    """Свободные слоты выбранного мастера на выбранную дату."""
    ctx = dialog_manager.dialog_data
    repo = BookingRepository(session)
    service = await repo.get_service(ctx["service_id"])
    if service is None:
        return {"slots": [], "has_slots": False}
    slots = await repo.get_available_slots(
        master_id=ctx["master_id"],
        day=date.fromisoformat(ctx["date"]),
        duration_minutes=service.duration_minutes,
    )
    return {
        "slots": [(dt.isoformat(), dt.strftime("%H:%M")) for dt in slots],
        "has_slots": bool(slots),
    }


async def confirm_getter(
    dialog_manager: DialogManager, session: AsyncSession, **_: Any
) -> dict[str, Any]:
    """Сводка бронирования для окна подтверждения."""
    ctx = dialog_manager.dialog_data
    settings = get_settings()
    repo = BookingRepository(session)
    service = await repo.get_service(ctx["service_id"])
    master = await repo.get_master(ctx["master_id"])
    starts_at = datetime.fromisoformat(ctx["slot"]).astimezone(settings.tz)
    return {
        "service": service.title if service else "—",
        "master": master.name if master else "—",
        "when": starts_at.strftime("%d.%m.%Y %H:%M"),
        "seats": ctx["seats"],
        "name": ctx["name"],
        "phone": ctx["phone"],
    }


# ---------------------------------------------------------------- callbacks


async def on_service_selected(
    _: CallbackQuery, __: Any, manager: DialogManager, item_id: str
) -> None:
    manager.dialog_data["service_id"] = int(item_id)
    await manager.next()


async def on_seats_selected(
    _: CallbackQuery, __: Any, manager: DialogManager, item_id: str
) -> None:
    manager.dialog_data["seats"] = int(item_id)
    await manager.next()


async def on_master_selected(
    _: CallbackQuery, __: Any, manager: DialogManager, item_id: str
) -> None:
    manager.dialog_data["master_id"] = int(item_id)
    await manager.next()


async def on_date_selected(
    callback: CallbackQuery, _: Any, manager: DialogManager, selected: date
) -> None:
    today = datetime.now(get_settings().tz).date()
    if selected < today:
        await callback.answer("Нельзя записаться на прошедшую дату", show_alert=True)
        return
    manager.dialog_data["date"] = selected.isoformat()
    await manager.next()


async def on_slot_selected(_: CallbackQuery, __: Any, manager: DialogManager, item_id: str) -> None:
    manager.dialog_data["slot"] = item_id
    await manager.next()


def name_check(text: str) -> str:
    """type_factory: валидация имени через Pydantic (поднимает ValueError)."""
    try:
        return ContactSchema.model_validate({"name": text, "phone": "+79990000000"}).name
    except ValidationError as exc:
        raise ValueError("bad name") from exc


def phone_check(text: str) -> str:
    """type_factory: валидация и нормализация телефона через Pydantic."""
    try:
        return ContactSchema.model_validate({"name": "stub", "phone": text}).phone
    except ValidationError as exc:
        raise ValueError("bad phone") from exc


async def on_name_success(
    _: Message, __: ManagedTextInput[str], manager: DialogManager, value: str
) -> None:
    manager.dialog_data["name"] = value
    await manager.next()


async def on_phone_success(
    _: Message, __: ManagedTextInput[str], manager: DialogManager, value: str
) -> None:
    manager.dialog_data["phone"] = value
    await manager.next()


async def on_name_error(message: Message, __: Any, ___: DialogManager, ____: ValueError) -> None:
    await message.answer("Имя должно содержать от 2 до 64 символов. Попробуйте ещё раз.")


async def on_phone_error(message: Message, __: Any, ___: DialogManager, ____: ValueError) -> None:
    await message.answer("Неверный формат номера. Пример: +79991234567 или 89991234567.")


async def on_confirm(callback: CallbackQuery, _: Button, manager: DialogManager) -> None:
    """Финальный шаг: транзакционное создание записи + уведомления."""
    session: AsyncSession = manager.middleware_data["session"]
    bot: Bot = manager.middleware_data["bot"]
    settings = get_settings()
    ctx = manager.dialog_data
    repo = BookingRepository(session)
    starts_at = datetime.fromisoformat(ctx["slot"])

    try:
        booking = await repo.create_booking(
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            client_name=ctx["name"],
            phone=ctx["phone"],
            service_id=ctx["service_id"],
            master_id=ctx["master_id"],
            starts_at=starts_at,
            seats_count=ctx["seats"],
        )
        await session.commit()
    except SlotOccupiedError:
        await session.rollback()
        await callback.answer(
            "Увы, этот слот только что заняли. Выберите другое время.",
            show_alert=True,
        )
        await manager.switch_to(BookingSG.time_slot)
        return
    except SQLAlchemyError:
        await session.rollback()
        logger.exception("DB error while creating booking (user=%s)", callback.from_user.id)
        await callback.answer(DB_ERROR_TEXT, show_alert=True)
        return

    # Напоминание клиенту до записи (фоновая задача Taskiq).
    try:
        await schedule_reminder(booking.id, callback.from_user.id, starts_at)
    except Exception:
        logger.exception("Failed to schedule reminder for booking %s", booking.id)

    # Уведомление администратору (не критично для клиента).
    starts_local = starts_at.astimezone(settings.tz)
    try:
        await bot.send_message(
            settings.admin_chat_id,
            f"🆕 Новая запись #{booking.id}\n"
            f"Клиент: {html.quote(ctx['name'])} ({html.quote(ctx['phone'])})\n"
            f"Когда: {starts_local:%d.%m.%Y %H:%M}\n"
            f"Мест: {ctx['seats']}",
        )
    except Exception:
        logger.exception("Failed to notify admin about booking %s", booking.id)

    if callback.message is not None:
        await callback.message.answer(
            f"✅ Вы записаны на {starts_local:%d.%m.%Y в %H:%M}. Ждём вас!\n"
            f"Посмотреть или отменить запись: /my"
        )
    await manager.done()


# ---------------------------------------------------------------- windows

booking_dialog = Dialog(
    Window(
        Const("Выберите услугу:"),
        Column(
            Select(
                Format("{item[1]}"),
                id="service",
                item_id_getter=operator.itemgetter(0),
                items="services",
                on_click=on_service_selected,
            ),
        ),
        Cancel(Const("❌ Отмена")),
        state=BookingSG.service,
        getter=services_getter,
    ),
    Window(
        Const("Сколько мест забронировать?"),
        Group(
            Select(
                Format("{item}"),
                id="seats",
                item_id_getter=str,
                items=[1, 2, 3, 4, 5, 6],
                on_click=on_seats_selected,
            ),
            width=3,
        ),
        Back(Const("⬅️ Назад")),
        state=BookingSG.seats,
    ),
    Window(
        Const("Выберите мастера:"),
        Column(
            Select(
                Format("{item[1]}"),
                id="master",
                item_id_getter=operator.itemgetter(0),
                items="masters",
                on_click=on_master_selected,
            ),
        ),
        Back(Const("⬅️ Назад")),
        state=BookingSG.master,
        getter=masters_getter,
    ),
    Window(
        Const("Выберите дату:"),
        Calendar(id="calendar", on_click=on_date_selected),
        Back(Const("⬅️ Назад")),
        state=BookingSG.calendar,
    ),
    Window(
        Const("Свободное время:", when="has_slots"),
        Const("На эту дату свободных слотов нет 😔", when=~F["has_slots"]),
        Group(
            Select(
                Format("{item[1]}"),
                id="slot",
                item_id_getter=operator.itemgetter(0),
                items="slots",
                on_click=on_slot_selected,
            ),
            width=4,
        ),
        Back(Const("⬅️ Назад")),
        state=BookingSG.time_slot,
        getter=slots_getter,
    ),
    Window(
        Const("Как вас зовут?"),
        TextInput(
            id="name_input",
            type_factory=name_check,
            on_success=on_name_success,
            on_error=on_name_error,
        ),
        Back(Const("⬅️ Назад")),
        state=BookingSG.name,
    ),
    Window(
        Const("Введите номер телефона (например, +79991234567):"),
        TextInput(
            id="phone_input",
            type_factory=phone_check,
            on_success=on_phone_success,
            on_error=on_phone_error,
        ),
        Back(Const("⬅️ Назад")),
        state=BookingSG.phone,
    ),
    Window(
        Format(
            "Проверьте данные записи:\n\n"
            "💇 Услуга: {service}\n"
            "👤 Мастер: {master}\n"
            "🗓 Когда: {when}\n"
            "🪑 Мест: {seats}\n"
            "📞 {name}, {phone}"
        ),
        Button(Const("✅ Подтвердить"), id="confirm", on_click=on_confirm),
        Back(Const("⬅️ Назад")),
        Cancel(Const("❌ Отмена")),
        state=BookingSG.confirm,
        getter=confirm_getter,
    ),
)


@router.message(CommandStart())
async def cmd_start(message: Message, dialog_manager: DialogManager) -> None:
    """Запуск сценария бронирования."""
    await dialog_manager.start(BookingSG.service, mode=StartMode.RESET_STACK)
