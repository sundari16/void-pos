from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from void_pos.ids import new_ulid
from void_pos.models import Base, Device, Location, MenuItem, Order, Org
from void_pos.sync import pull_events, push_events

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed(s: Session) -> dict[str, str]:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    item = MenuItem(location_id=loc.id, name="Masala Dosa", base_price_cents=1850)
    s.add_all([device, item])
    s.flush()
    s.commit()
    return {"loc": loc.id, "device": device.id, "item": item.id}


def _placed_event(ids: dict[str, str], *, seq: int) -> dict[str, Any]:
    return {
        "event_id": new_ulid(),
        "device_id": ids["device"],
        "seq": seq,
        "location_id": ids["loc"],
        "business_date": TODAY,
        "occurred_at": T0 + timedelta(seconds=seq),
        "type": "order.placed",
        "payload": {
            "order_id": new_ulid(),
            "order_number": 1000 + seq,
            "channel": "pos",
            "fulfillment": "pickup",
            "lines": [
                {
                    "id": new_ulid(),
                    "menu_item_id": ids["item"],
                    "quantity": 1,
                    "name_snapshot": "Masala Dosa",
                    "unit_price_cents": 1850,
                }
            ],
        },
    }


def test_push_applies_and_returns_cursor() -> None:
    with _session() as s:
        ids = _seed(s)
        ev = _placed_event(ids, seq=1)
        result = push_events(s, [ev])
        s.commit()

        assert [a["status"] for a in result.acks] == ["applied"]
        assert result.cursor is not None
        assert s.get(Order, ev["payload"]["order_id"]) is not None


def test_push_is_idempotent() -> None:
    with _session() as s:
        ids = _seed(s)
        ev = _placed_event(ids, seq=1)
        assert push_events(s, [ev]).acks[0]["status"] == "applied"
        s.commit()
        assert push_events(s, [ev]).acks[0]["status"] == "duplicate"
        s.commit()


def test_pull_paginates_in_arrival_order() -> None:
    with _session() as s:
        ids = _seed(s)
        push_events(s, [_placed_event(ids, seq=1), _placed_event(ids, seq=2)])
        s.commit()

        first = pull_events(s, None, limit=1)
        assert len(first.events) == 1
        assert first.has_more is True

        second = pull_events(s, first.next_cursor, limit=10)
        assert len(second.events) == 1
        assert second.has_more is False
        assert first.events[0]["event_id"] != second.events[0]["event_id"]


def test_pull_excludes_the_requesting_device() -> None:
    with _session() as s:
        ids = _seed(s)
        push_events(s, [_placed_event(ids, seq=1)])
        s.commit()

        res = pull_events(s, None, exclude_device_id=ids["device"])
        assert res.events == []
