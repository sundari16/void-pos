"""Device sync: push device-generated events to the server and pull the venue's others.

Pull is scoped to the device's location and pages by ``sync_seq``, a per-location number
given to each event after it commits. Each pull first numbers the location's committed,
unnumbered events (in arrival order) under the location's counter-row lock, then reads
past the till's cursor. A transaction that commits late can only ever get a *higher*
number than anything already handed out, so a pull never steps past an event, however
the writes interleave. Writers never touch the counter, so they never wait on each other
or on a pull.

Each till keeps its own pull cursor; a push doesn't move it. Push is idempotent (the apply
layer skips a known ``event_id``), so a retried push after a flaky connection is safe, and
one bad event is rejected without failing the rest of the batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import Forbidden, InvalidRequest
from ..events import ApplyResult, apply_events
from ..models import Device, Event, SyncCounter

MAX_PULL = 500


@dataclass
class PullResult:
    events: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool


def _decode_cursor(cursor: str) -> int:
    if not cursor.isdigit():
        raise InvalidRequest("that sync cursor isn't valid", code="invalid_cursor")
    return int(cursor)


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


def sequence_events(session: Session, location_id: str) -> int:
    """Give the location's committed, unnumbered events their sync positions, in arrival
    order. Returns how many were numbered. The caller commits."""
    counter = session.get(SyncCounter, location_id, with_for_update=True)
    if counter is None:  # a location made outside create_location (tests, old data)
        counter = SyncCounter(location_id=location_id, last_seq=0)
        session.add(counter)
        session.flush()
    pending = session.scalars(
        select(Event)
        .where(Event.location_id == location_id, Event.sync_seq.is_(None))
        .order_by(Event.received_at, Event.event_id)
    ).all()
    for event in pending:
        counter.last_seq += 1
        event.sync_seq = counter.last_seq
    session.flush()
    return len(pending)


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
    """Events at ``location_id`` after ``cursor`` (a ``sync_seq``), in commit order.
    Numbers newly committed events first, so the caller must commit afterwards."""
    after = _decode_cursor(cursor) if cursor else 0
    limit = max(1, min(limit, MAX_PULL))
    sequence_events(session, location_id)

    stmt = select(Event).where(Event.location_id == location_id, Event.sync_seq > after)
    if exclude_device_id:
        stmt = stmt.where(Event.device_id != exclude_device_id)
    rows = list(session.scalars(stmt.order_by(Event.sync_seq).limit(limit + 1)).all())
    has_more = len(rows) > limit
    rows = rows[:limit]
    return PullResult(
        events=[_serialize(e) for e in rows],
        next_cursor=str(rows[-1].sync_seq) if rows else cursor,
        has_more=has_more,
    )
