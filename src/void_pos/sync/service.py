"""Device sync: push device-generated events to the server and pull everyone else's.

The cursor orders by ``received_at`` (server arrival), not device time, so a device that
comes online and uploads old offline events never slips behind another device's cursor.
Push is idempotent (the apply layer skips a known ``event_id``), so a retried push after a
flaky connection is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..events import apply_events
from ..models import Event

_CURSOR_SEP = "|"


@dataclass
class PushResult:
    acks: list[dict[str, str]]
    cursor: str | None


@dataclass
class PullResult:
    events: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


def _encode_cursor(event: Event) -> str:
    return f"{event.received_at.isoformat()}{_CURSOR_SEP}{event.event_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    ts, _, event_id = cursor.partition(_CURSOR_SEP)
    return datetime.fromisoformat(ts), event_id


def _serialize(event: Event) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "device_id": event.device_id,
        "seq": event.seq,
        "location_id": event.location_id,
        "business_date": event.business_date.isoformat(),
        "occurred_at": event.occurred_at.isoformat(),
        "type": event.type,
        "payload": event.payload,
    }


def push_events(session: Session, incoming: list[dict[str, Any]]) -> PushResult:
    """Apply a batch of device events idempotently and return per-event acks plus the
    server's current cursor, from which the device can then pull everyone else's events."""
    results = apply_events(session, incoming)
    session.flush()
    latest = session.scalars(
        select(Event).order_by(Event.received_at.desc(), Event.event_id.desc()).limit(1)
    ).first()
    return PushResult(
        acks=[{"event_id": r.event_id, "status": r.status} for r in results],
        cursor=_encode_cursor(latest) if latest is not None else None,
    )


def pull_events(
    session: Session,
    cursor: str | None = None,
    *,
    limit: int = 100,
    exclude_device_id: str | None = None,
) -> PullResult:
    """Return events the device has not seen, in server-arrival order, the newest last."""
    limit = max(1, min(limit, 500))
    stmt = select(Event)
    if cursor:
        ts, event_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(Event.received_at > ts, and_(Event.received_at == ts, Event.event_id > event_id))
        )
    if exclude_device_id:
        stmt = stmt.where(Event.device_id != exclude_device_id)
    stmt = stmt.order_by(Event.received_at, Event.event_id).limit(limit + 1)

    rows = list(session.scalars(stmt).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return PullResult(
        events=[_serialize(e) for e in rows],
        next_cursor=_encode_cursor(rows[-1]) if rows else cursor,
        has_more=has_more,
    )
