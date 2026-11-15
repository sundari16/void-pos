from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from void_pos.events import ingest_event
from void_pos.export import export_csv, export_location
from void_pos.ids import new_ulid
from void_pos.models import Base, Customer, Device, Location, MenuItem, Org
from void_pos.payments import CashProvider, pay_order

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _seed_with_activity(s: Session) -> dict[str, Any]:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    customer = Customer(org_id=org.id, location_id=loc.id, name="Asha", phone="+61400000000")
    item = MenuItem(location_id=loc.id, name="Masala Dosa", category="Mains", base_price_cents=1850)
    s.add_all([device, customer, item])
    s.flush()

    order_id = new_ulid()
    ingest_event(
        s,
        {
            "event_id": new_ulid(),
            "device_id": device.id,
            "seq": 1,
            "location_id": loc.id,
            "business_date": TODAY,
            "occurred_at": T0,
            "type": "order.placed",
            "payload": {
                "order_id": order_id,
                "order_number": 1000,
                "customer_id": customer.id,
                "channel": "pos",
                "fulfillment": "pickup",
                "lines": [
                    {
                        "id": new_ulid(),
                        "menu_item_id": item.id,
                        "quantity": 2,
                        "name_snapshot": "Masala Dosa",
                        "unit_price_cents": 1850,
                    }
                ],
            },
        },
    )
    pay_order(s, order_id, CashProvider())  # emits order.paid (device seq advances)
    ingest_event(
        s,
        {
            "event_id": new_ulid(),
            "device_id": device.id,
            "seq": 3,
            "location_id": loc.id,
            "business_date": TODAY,
            "occurred_at": T0,
            "type": "loyalty.recorded",
            "payload": {
                "id": new_ulid(),
                "customer_id": customer.id,
                "points_delta": 10,
                "reason": "earn",
            },
        },
    )
    s.commit()
    return {"loc": loc.id, "customer": customer.id, "order_id": order_id}


def test_export_bundle_is_complete() -> None:
    with _session() as s:
        d = _seed_with_activity(s)
        bundle = export_location(s, d["loc"])

        assert bundle["location"]["id"] == d["loc"]
        assert any(i["name"] == "Masala Dosa" for i in bundle["menu"]["items"])
        customer = next(c for c in bundle["customers"] if c["id"] == d["customer"])
        assert customer["loyalty_balance"] == 10
        order = next(o for o in bundle["orders"] if o["id"] == d["order_id"])
        assert len(order["lines"]) == 1
        assert len(order["payments"]) == 1
        assert order["payments"][0]["amount_cents"] == 3700
        assert len(bundle["loyalty_ledger"]) == 1


def test_export_is_json_serializable() -> None:
    with _session() as s:
        d = _seed_with_activity(s)
        json.dumps(export_location(s, d["loc"]))  # no Decimal/datetime leaks


def test_export_csv_orders_has_header_and_one_row() -> None:
    with _session() as s:
        d = _seed_with_activity(s)
        rows = export_csv(s, d["loc"], "orders").strip().splitlines()
        assert rows[0].startswith("id,order_number")
        assert len(rows) == 2


def test_export_unknown_entity_raises() -> None:
    with _session() as s:
        d = _seed_with_activity(s)
        with pytest.raises(KeyError):
            export_csv(s, d["loc"], "bogus")


def test_export_unknown_location_raises() -> None:
    with _session() as s, pytest.raises(LookupError):
        export_location(s, "00000000000000000000000000")
