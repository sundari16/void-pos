from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from support import make_engine
from void_pos.errors import Forbidden, InvalidRequest
from void_pos.ids import new_ulid
from void_pos.models import Device, Location, MenuItem, Order, Org
from void_pos.sync import pull_events, push_events

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = make_engine()
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


def _device(s: Session, ids: dict[str, str]) -> Device:
    device = s.get(Device, ids["device"])
    assert device is not None
    return device


def test_push_applies_events() -> None:
    with _session() as s:
        ids = _seed(s)
        ev = _placed_event(ids, seq=1)
        results = push_events(s, [ev], device=_device(s, ids))
        s.commit()

        assert [r.status for r in results] == ["applied"]
        assert s.get(Order, ev["payload"]["order_id"]) is not None


def test_push_is_idempotent() -> None:
    with _session() as s:
        ids = _seed(s)
        ev = _placed_event(ids, seq=1)
        assert push_events(s, [ev], device=_device(s, ids))[0].status == "applied"
        s.commit()
        assert push_events(s, [ev], device=_device(s, ids))[0].status == "duplicate"
        s.commit()


def test_a_bad_event_is_rejected_without_failing_the_batch() -> None:
    with _session() as s:
        ids = _seed(s)
        good, bad = _placed_event(ids, seq=1), _placed_event(ids, seq=2)
        bad["payload"]["lines"] = []
        results = push_events(s, [good, bad], device=_device(s, ids))
        s.commit()

        assert [r.status for r in results] == ["applied", "rejected"]
        assert results[1].code == "invalid_payload"
        assert s.get(Order, good["payload"]["order_id"]) is not None
        assert s.get(Order, bad["payload"]["order_id"]) is None


def test_push_refuses_another_devices_events() -> None:
    with _session() as s:
        ids = _seed(s)
        other = Device(
            location_id=ids["loc"], name="Till 2", order_number_start=2000, order_number_end=2999
        )
        s.add(other)
        s.commit()
        with pytest.raises(Forbidden):
            push_events(s, [_placed_event(ids, seq=1)], device=other)


def test_pull_paginates_in_arrival_order() -> None:
    with _session() as s:
        ids = _seed(s)
        push_events(s, [_placed_event(ids, seq=1), _placed_event(ids, seq=2)], device=_device(s, ids))
        s.commit()

        first = pull_events(s, location_id=ids["loc"], limit=1)
        assert len(first.events) == 1
        assert first.has_more is True

        second = pull_events(s, location_id=ids["loc"], cursor=first.next_cursor, limit=10)
        assert len(second.events) == 1
        assert second.has_more is False
        assert first.events[0]["event_id"] != second.events[0]["event_id"]


def test_pull_excludes_the_requesting_device() -> None:
    with _session() as s:
        ids = _seed(s)
        push_events(s, [_placed_event(ids, seq=1)], device=_device(s, ids))
        s.commit()

        res = pull_events(s, location_id=ids["loc"], exclude_device_id=ids["device"])
        assert res.events == []


def test_pull_never_returns_another_locations_events() -> None:
    with _session() as s:
        ids = _seed(s)
        push_events(s, [_placed_event(ids, seq=1)], device=_device(s, ids))
        s.commit()
        elsewhere = Location(org_id=s.get(Location, ids["loc"]).org_id, name="Gold Coast")
        s.add(elsewhere)
        s.commit()

        assert pull_events(s, location_id=elsewhere.id).events == []
        assert len(pull_events(s, location_id=ids["loc"]).events) == 1


def test_a_malformed_cursor_is_an_invalid_request() -> None:
    with _session() as s:
        ids = _seed(s)
        with pytest.raises(InvalidRequest) as exc:
            pull_events(s, location_id=ids["loc"], cursor="not-a-cursor")
        assert exc.value.code == "invalid_cursor"
