from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import Base, Device, Location, MenuItem, Order, Org, Payment
from void_pos.payments import CashProvider, ExternalProvider, order_total_cents, pay_order

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _place(s: Session, *, quantity: int = 2, price: int = 1850) -> str:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    item = MenuItem(location_id=loc.id, name="Masala Dosa", base_price_cents=price)
    s.add_all([device, item])
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
                "channel": "pos",
                "fulfillment": "pickup",
                "lines": [
                    {
                        "id": new_ulid(),
                        "menu_item_id": item.id,
                        "quantity": quantity,
                        "name_snapshot": "Masala Dosa",
                        "unit_price_cents": price,
                    }
                ],
            },
        },
    )
    s.commit()
    return order_id


def test_order_total_sums_lines() -> None:
    with _session() as s:
        order_id = _place(s, quantity=2, price=1850)
        assert order_total_cents(s, order_id) == 3700


def test_cash_payment_captures_and_marks_paid() -> None:
    with _session() as s:
        order_id = _place(s)
        payment = pay_order(s, order_id, CashProvider())
        s.commit()

        assert payment.provider == "cash"
        assert payment.external_ref is None
        assert payment.amount_cents == 3700  # defaulted to the order total
        assert s.get(Order, order_id).state == "paid"
        assert s.scalar(select(func.count()).select_from(Payment)) == 1


def test_external_payment_records_ref_not_as_key() -> None:
    with _session() as s:
        order_id = _place(s)
        payment = pay_order(
            s, order_id, ExternalProvider(external_ref="pi_test_123"), amount_cents=3700
        )
        s.commit()

        assert payment.provider == "external"
        assert payment.external_ref == "pi_test_123"
        assert payment.id != "pi_test_123"  # keyed on our own ULID, never the provider id
        assert s.get(Order, order_id).state == "paid"


def test_pay_unknown_order_raises() -> None:
    with _session() as s:
        _place(s)
        with pytest.raises(LookupError):
            pay_order(s, "00000000000000000000000000", CashProvider())
