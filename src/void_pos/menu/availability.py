"""When an item or modifier group can be sold: its flag and an optional daily window.

A window runs from ``available_from`` (inclusive) to ``available_to`` (exclusive) in the
location's local time. A window that ends before it starts runs past midnight (22:00 to
02:00), and an open end is unbounded (from 17:00 until midnight; from midnight to 11:00).
"""

from __future__ import annotations

from datetime import time

from ..errors import InvalidRequest


def in_window(start: time | None, end: time | None, at: time) -> bool:
    if start is None:
        return end is None or at < end
    if end is None:
        return at >= start
    if start < end:
        return start <= at < end
    return at >= start or at < end


def check_window(start: time | None, end: time | None) -> None:
    if start is not None and end is not None and start == end:
        raise InvalidRequest(
            "available_from and available_to can't be the same time; clear both to sell "
            "all day",
            code="invalid_time_window",
        )
