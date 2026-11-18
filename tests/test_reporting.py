from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.orm import Session

from support import make_engine
from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import Device, Location, Org
from void_pos.reporting import shift_closeout

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = make_engine()
    return Session(engine)


def _seed_day(s: Session) -> dict[str, Any]:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    s.add(device)
    s.flush()
    ids = {"loc": loc.id, "device": device.id}

    def ev(*, seq: int, type: str, payload: dict[str, Any]) -> None:
        ingest_event(
            s,
            {
                "event_id": new_ulid(),
                "device_id": device.id,
                "seq": seq,
                "location_id": loc.id,
                "business_date": TODAY,
                "occurred_at": T0 + timedelta(seconds=seq),
                "type": type,
                "payload": payload,
            },
        )

    def line(*, qty: int, price: int, category: str) -> dict[str, Any]:
        return {
            "id": new_ulid(),
            "quantity": qty,
            "name_snapshot": category,
            "unit_price_cents": price,
            "category_snapshot": category,
        }

    order_a, order_b, order_c = new_ulid(), new_ulid(), new_ulid()
    # A: 2x Mains (3700), paid cash
    ev(seq=1, type="order.placed", payload={
        "order_id": order_a, "order_number": 1000, "channel": "pos", "fulfillment": "pickup",
        "lines": [line(qty=2, price=1850, category="Mains")]})
    ev(seq=2, type="order.paid", payload={
        "order_id": order_a,
        "payment": {"id": new_ulid(), "provider": "cash", "amount_cents": 3700}})
    # B: 1x Drinks (600), paid external
    ev(seq=3, type="order.placed", payload={
        "order_id": order_b, "order_number": 1001, "channel": "web", "fulfillment": "pickup",
        "lines": [line(qty=1, price=600, category="Drinks")]})
    ev(seq=4, type="order.paid", payload={
        "order_id": order_b,
        "payment": {"id": new_ulid(), "provider": "external", "external_ref": "pi_1",
                    "amount_cents": 600}})
    # C: 1x Mains, voided (must not count)
    ev(seq=5, type="order.placed", payload={
        "order_id": order_c, "order_number": 1002, "channel": "pos", "fulfillment": "pickup",
        "lines": [line(qty=1, price=1850, category="Mains")]})
    ev(seq=6, type="order.voided", payload={"order_id": order_c})
    s.commit()
    return ids


def test_shift_closeout_totals() -> None:
    with _session() as s:
        ids = _seed_day(s)
        report = shift_closeout(s, ids["loc"], TODAY)

        assert report["gross_sales_cents"] == 4300
        assert report["payments_by_provider"] == {"cash": 3700, "external": 600}
        assert report["orders"] == {"total": 3, "open": 0, "sent": 0, "paid": 2, "voided": 1}


def test_revenue_class_excludes_unpaid_orders() -> None:
    with _session() as s:
        ids = _seed_day(s)
        report = shift_closeout(s, ids["loc"], TODAY)

        # Only the paid Mains order (3700) counts — the voided one (1850) does not.
        assert report["revenue_by_class_cents"] == {"Mains": 3700, "Drinks": 600}


def test_closeout_unknown_location_raises() -> None:
    with _session() as s, pytest.raises(LookupError):
        shift_closeout(s, "00000000000000000000000000", TODAY)
