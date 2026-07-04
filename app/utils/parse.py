"""Парсинг пользовательского ввода админ-меню (часы, интервалы дат)."""

from __future__ import annotations

import re
from datetime import date, time

HOURS_RE = re.compile(r"^\s*(\d{1,2})[:.](\d{2})\s*[-–—]\s*(\d{1,2})[:.](\d{2})\s*$")
DATE_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s*$")
OFF_WORDS = {"выходной", "выходная", "off", "-"}


def parse_hours(text: str) -> tuple[time, time] | None:
    """«10:00-19:00» → (time(10), time(19)); None — не распознано/некорректно."""
    match = HOURS_RE.match(text)
    if match is None:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
        return None
    start, end = time(h1, m1), time(h2, m2)
    if start >= end:
        return None
    return start, end


def is_day_off(text: str) -> bool:
    """Ввод означает «сделать день выходным»."""
    return text.strip().lower() in OFF_WORDS


def _parse_date(raw: str, today: date) -> date | None:
    match = DATE_RE.match(raw)
    if match is None:
        return None
    day, month, year_raw = match.groups()
    year = int(year_raw) if year_raw else today.year
    try:
        parsed = date(year, int(month), int(day))
    except ValueError:
        return None
    # Без года: прошедшая дата означает следующий год (отпуск в январе, ввод в декабре).
    if year_raw is None and parsed < today:
        parsed = date(year + 1, parsed.month, parsed.day)
    return parsed


def parse_date_range(text: str, today: date) -> tuple[date, date] | None:
    """«15.07-20.07», «15.07» или «15.07.2026-20.07.2026» → (от, до) включительно."""
    parts = re.split(r"\s*[-–—]\s*", text.strip())
    if len(parts) == 1:
        single = _parse_date(parts[0], today)
        return (single, single) if single else None
    if len(parts) != 2:
        return None
    date_from = _parse_date(parts[0], today)
    date_to = _parse_date(parts[1], today)
    if date_from is None or date_to is None or date_from > date_to:
        return None
    return date_from, date_to
