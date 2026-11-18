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

from sqlalchemy import select
from sqlalchemy.orm import Session

from .clock import utcnow
from .devices import next_order_number
from .errors import Conflict, InvalidRequest
from .events import ApplyResult, ingest_event
from .models import Device, Event

# Refusals that clash with what is already recorded, rather than malformed input.
_CONFLICTS = frozenset({"seq_conflict", "order_exists", "order_number_used", "foreign_order"})


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
        detail = result.detail or "the change was rejected"
        code = result.code or "invalid_request"
        if code in _CONFLICTS:
            # Tell a till whose counters fell behind (its storage was cleared, or two tabs
            # share it) where the server is, so it can carry on from there.
            extra: dict[str, Any] = {}
            if code == "seq_conflict":
                extra["last_seq"] = device.last_seq
            if code == "order_number_used":
                extra["next_order_number"] = next_order_number(session, device)
            raise Conflict(detail, code=code, **extra)
        raise InvalidRequest(detail, code=code)
    return result


def ensure_seq_free(session: Session, device: Device, command: Command) -> None:
    """Refuse a reused seq up front, for commands with a side effect (printing) that
    happens before the event is written."""
    taken = session.scalar(
        select(Event.event_id)
        .where(Event.device_id == device.id, Event.seq == command.seq)
        .limit(1)
    )
    if taken is not None:
        raise Conflict(
            f"seq {command.seq} is already used by this device",
            code="seq_conflict",
            last_seq=device.last_seq,
        )
