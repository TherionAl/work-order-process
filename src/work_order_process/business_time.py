"""Business-time calculation for ticket node durations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


DEFAULT_WORK_SESSIONS = (
    (time(9, 0), time(11, 50)),
    (time(13, 40), time(18, 0)),
)


@dataclass(frozen=True)
class WorkCalendar:
    """China workday calendar with JSON overrides for holidays and make-up days."""

    overrides: dict[date, bool]
    names: dict[date, str]
    work_sessions: tuple[tuple[time, time], ...] = DEFAULT_WORK_SESSIONS

    @classmethod
    def from_json(cls, path: Path) -> "WorkCalendar":
        data = json.loads(path.read_text(encoding="utf-8"))
        overrides: dict[date, bool] = {}
        names: dict[date, str] = {}

        days = data.get("days", data)
        if not isinstance(days, dict):
            raise ValueError(f"Invalid calendar JSON: {path}")

        for day_text, item in days.items():
            day = date.fromisoformat(day_text)
            if isinstance(item, dict):
                overrides[day] = bool(item.get("is_workday"))
                if item.get("name"):
                    names[day] = str(item["name"])
            else:
                overrides[day] = bool(item)

        sessions = tuple(_parse_session(item) for item in data.get("work_sessions", []))
        return cls(overrides=overrides, names=names, work_sessions=sessions or DEFAULT_WORK_SESSIONS)

    def is_workday(self, day: date) -> bool:
        if day in self.overrides:
            return self.overrides[day]
        return day.weekday() < 5

    def day_name(self, day: date) -> str | None:
        return self.names.get(day)


def business_seconds_between(start: datetime, end: datetime, calendar: WorkCalendar) -> int:
    """Return working seconds in [start, end)."""

    if end <= start:
        return 0

    total = 0
    current_day = start.date()
    last_day = end.date()
    while current_day <= last_day:
        if calendar.is_workday(current_day):
            for session_start, session_end in calendar.work_sessions:
                window_start = datetime.combine(current_day, session_start)
                window_end = datetime.combine(current_day, session_end)
                overlap_start = max(start, window_start)
                overlap_end = min(end, window_end)
                if overlap_end > overlap_start:
                    total += int((overlap_end - overlap_start).total_seconds())
        current_day += timedelta(days=1)
    return total


def business_minutes_between(start: datetime, end: datetime, calendar: WorkCalendar) -> int:
    return business_seconds_between(start, end, calendar) // 60


def _parse_session(item: Any) -> tuple[time, time]:
    if not isinstance(item, str) or "-" not in item:
        raise ValueError(f"Invalid work session: {item!r}")
    start_text, end_text = item.split("-", 1)
    return time.fromisoformat(start_text), time.fromisoformat(end_text)
