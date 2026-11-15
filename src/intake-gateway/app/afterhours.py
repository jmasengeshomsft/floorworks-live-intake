from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings

_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

DEFAULT_NOTE = (
    "AFTER HOURS: the office is closed. Do NOT offer to connect the caller to a person. "
    "Collect name, equipment and the problem, tell them someone will follow up next business day. "
    "If it is an emergency, tell them to hang up and call emergency services."
)


def _day_set(spec: str) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (x.strip()[:3].title() for x in part.split("-", 1))
            i, j = _DAYS.index(a), _DAYS.index(b)
            out.update(range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)])
        elif part:
            out.add(_DAYS.index(part[:3].title()))
    return out


def is_active(s: Settings, now: datetime | None = None) -> bool:
    mode = s.after_hours
    if mode == "on":
        return True
    if mode != "auto":
        return False
    now = (now or datetime.now(ZoneInfo(s.service_tz))).astimezone(ZoneInfo(s.service_tz))
    if now.weekday() not in _day_set(s.service_days):
        return True
    start, end = (datetime.strptime(x.strip(), "%H:%M").time() for x in s.service_hours.split("-"))
    return not (start <= now.time() < end)


def note(s: Settings) -> str:
    return s.after_hours_note or DEFAULT_NOTE
