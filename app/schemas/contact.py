"""Pydantic-схемы валидации контактных данных."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

PHONE_PATTERN = r"^\+?[78]\d{10}$"


class ContactSchema(BaseModel):
    """Имя и телефон клиента."""

    name: str = Field(min_length=2, max_length=64)
    phone: str = Field(pattern=PHONE_PATTERN)

    @field_validator("phone", mode="before")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        """Убирает пробелы, скобки и дефисы перед проверкой регуляркой."""
        return "".join(ch for ch in str(value) if ch.isdigit() or ch == "+")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()
