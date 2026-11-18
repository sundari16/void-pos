"""Device sync: push device-generated events to the server and pull the venue's others.

Pull is scoped to the device's location and ordered by ``received_at`` (server arrival),
not device time, so a till that comes online and uploads old offline events never slips
behind another till's cursor. Each till keeps its own pull cursor; a push doesn't move
it, because events from other tills can land between its last pull and its push. Push is
idempotent (the apply layer skips a known ``event_id``), so a retried push after a flaky
connection is safe, and one bad event is rejected without failing the rest of the batch.

Known limit: ``received_at`` is taken when an event is flushed, not when it commits, so
two transactions committing out of order within the same few milliseconds could let a
pull step past the earlier one. Commands keep that window minimal (the kitchen ticket
prints before its event is written); a commit-ordered sequence should replace the
timestamp cursor before tills rely on pull for offline catch-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from ..errors import Forbidden, InvalidRequest
from ..events import ApplyResult, apply_events
from ..models import Device, Event

_CURSOR_SEP = "|"
MAX_PULL = 500


@dataclass
class PullResult:
    events: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


def _encode_cursor(event: Event) -> str:
    return f"{event.received_at.isoformat()}{_CURSOR_SEP}{event.event_id}"


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    ts, sep, event_id = cursor.partition(_CURSOR_SEP)
    try:
        if not sep or not event_id:
            raise ValueError(cursor)
        return datetime.fromisoformat(ts), event_id
    except ValueError as exc:
        raise InvalidRequest("that sync cursor isn't valid", code="invalid_cursor") from exc


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


def push_events(
    session: Session, incoming: list[dict[str, Any]], *, device: Device
) -> list[ApplyResult]:
    """Apply a batch of this device's events idempotently; one result per event.

    A till may only push its own events: anything claiming another device or location
    refuses the whole batch, since that is a client bug or a forgery, not bad data."""
    for event in incoming:
        if event["device_id"] != device.id or event["location_id"] != device.location_id:
            raise Forbidden(
                "a till can only push its own events for its own location",
                code="foreign_event",
                event_id=event["event_id"],
            )
    results = apply_events(session, incoming)
    session.flush()
    return results


def pull_events(
    session: Session,
    *,
    location_id: str,
    cursor: str | None = None,
    limit: int = 100,
    exclude_device_id: str | None = None,
) -> PullResult:
    """Events at ``location_id`` the caller has not seen, in server-arrival order."""
    limit = max(1, min(limit, MAX_PULL))
    stmt = select(Event).where(Event.location_id == location_id)
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
