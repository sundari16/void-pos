"""The online write path: one till command becomes one event.

Every command carries ids the till minted (``event_id`` and its ``seq``, plus the order
or payment id), so a retry after a dropped connection replays to the original result
instead of placing, printing or charging twice. Commands go through ``ingest_event``,
the same path offline sync uses, so both write paths share one set of rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from .clock import utcnow
from .errors import Conflict, InvalidRequest
from .events import ApplyResult, ingest_event
from .models import Device, Event


@dataclass(frozen=True)
class Command:
    """The ids a till minted for one change."""

    event_id: str
    seq: int
    occurred_at: datetime | None = None  # the till's clock; the server's when absent


def find_replay(
    session: Session, command: Command, *, device: Device, event_type: str, order_id: str
) -> Event | None:
    """The event this command already produced, if it is a retry. An event id reused for
    a different change is a client bug, so it is refused rather than guessed at."""
    event = session.get(Event, command.event_id)
    if event is None:
        return None
    if (
        event.device_id != device.id
        or event.type != event_type
        or event.payload.get("order_id") != order_id
    ):
        raise Conflict(
            "this event_id was already used for a different change", code="event_id_reused"
        )
    return event


def record(
    session: Session,
    device: Device,
    command: Command,
    *,
    event_type: str,
    payload: dict[str, Any],
    business_date: date,
) -> ApplyResult:
    """Append the command's event and project it, or raise why it was refused."""
    result = ingest_event(
        session,
        {
            "event_id": command.event_id,
            "device_id": device.id,
            "seq": command.seq,
            "location_id": device.location_id,
            "business_date": business_date,
            "occurred_at": command.occurred_at or utcnow(),
            "type": event_type,
            "payload": payload,
        },
    )
    if result.status == "rejected":
        if result.code == "seq_conflict":
            # The till's counter fell behind (e.g. its storage was cleared): tell it where
            # the server is so it can carry on from there.
            raise Conflict(
                result.detail or "seq already used", code="seq_conflict", last_seq=device.last_seq
            )
        raise InvalidRequest(
            result.detail or "the change was rejected", code=result.code or "invalid_request"
        )
    return result
