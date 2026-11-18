"""Time helpers. Storage is UTC; business dates follow the location's own calendar."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_aware(value: datetime) -> datetime:
    """SQLite hands timezone-aware columns back naive; every stored time is UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def local_date(timezone: str, at: datetime | None = None) -> date:
    """The calendar date at ``at`` (default: now) in ``timezone``. An order rung up at
    8am in Brisbane is still the 18th there, even though it is the 17th in UTC."""
    return as_aware(at or utcnow()).astimezone(ZoneInfo(timezone)).date()
