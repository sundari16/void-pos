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

    def line(
        *, qty: int, price: int, category: str | None, parent: str | None = None
    ) -> dict[str, Any]:
        return {
            "id": new_ulid(),
            "parent_line_id": parent,
            "quantity": qty,
            "name_snapshot": category or "modifier",
            "unit_price_cents": price,
            "category_snapshot": category,
        }

    order_a, order_b, order_c, order_d = new_ulid(), new_ulid(), new_ulid(), new_ulid()
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
    # D: 2x Breads (400) each with butter (100, no category: counts as Breads) and a dip
    # (50, its own Extras class) = 2 x 550 = 1100, paid cash
    naan = line(qty=2, price=400, category="Breads")
    ev(seq=7, type="order.placed", payload={
        "order_id": order_d, "order_number": 1003, "channel": "pos", "fulfillment": "pickup",
        "lines": [naan, line(qty=1, price=100, category=None, parent=naan["id"]),
                  line(qty=1, price=50, category="Extras", parent=naan["id"])]})
    ev(seq=8, type="order.paid", payload={
        "order_id": order_d,
        "payment": {"id": new_ulid(), "provider": "cash", "amount_cents": 1100}})
    s.commit()
    return ids


def test_shift_closeout_totals() -> None:
    with _session() as s:
        ids = _seed_day(s)
        report = shift_closeout(s, ids["loc"], TODAY)

        assert report["gross_sales_cents"] == 5400
        # No tax rates on these lines, so no GST; nothing left owing on the day.
        assert (report["paid_sales_cents"], report["gst_cents"], report["open_balance_cents"]) == (
            5400,
            0,
            0,
        )
        assert report["payments_by_provider"] == {"cash": 4800, "external": 600}
        assert report["orders"] == {"total": 4, "open": 0, "sent": 0, "paid": 3, "voided": 1, "refunded": 0}


def test_revenue_class_excludes_unpaid_orders() -> None:
    with _session() as s:
        ids = _seed_day(s)
        report = shift_closeout(s, ids["loc"], TODAY)

        # Only the paid Mains order (3700) counts — the voided one (1850) does not.
        # Modifiers count once per unit of their item, in their item's class unless they
        # carry their own.
        assert report["revenue_by_class_cents"] == {
            "Mains": 3700,
            "Drinks": 600,
            "Breads": 1000,
            "Extras": 100,
        }
        assert sum(report["revenue_by_class_cents"].values()) == report["gross_sales_cents"]


def test_closeout_unknown_location_raises() -> None:
    with _session() as s, pytest.raises(LookupError):
        shift_closeout(s, "00000000000000000000000000", TODAY)


def test_revenue_by_class_handles_big_extended_quantities() -> None:
    # 999 items x 999 of a $100 modifier is beyond a 32-bit sum (PostgreSQL integer).
    with _session() as s:
        ids = _seed_day(s)
        item_line, order_id = new_ulid(), new_ulid()
        for seq, type_, payload in (
            (9, "order.placed", {
                "order_id": order_id, "order_number": 1004, "channel": "pos",
                "fulfillment": "pickup",
                "lines": [
                    {"id": item_line, "quantity": 999, "name_snapshot": "Party", "unit_price_cents": 0,
                     "category_snapshot": "Catering"},
                    {"id": new_ulid(), "parent_line_id": item_line, "quantity": 999,
                     "name_snapshot": "Tray", "unit_price_cents": 10_000},
                ]}),
            (10, "order.paid", {"order_id": order_id}),
        ):
            ingest_event(s, {
                "event_id": new_ulid(), "device_id": ids["device"], "seq": seq,
                "location_id": ids["loc"], "business_date": TODAY,
                "occurred_at": T0 + timedelta(seconds=seq), "type": type_, "payload": payload,
            })
        s.commit()
        assert shift_closeout(s, ids["loc"], TODAY)["revenue_by_class_cents"]["Catering"] == (
            999 * 999 * 10_000
        )


def test_closeout_reports_gst_and_what_is_still_owing() -> None:
    with _session() as s:
        ids = _seed_day(s)

        def ev(seq: int, type_: str, payload: dict[str, Any]) -> None:
            ingest_event(s, {
                "event_id": new_ulid(), "device_id": ids["device"], "seq": seq,
                "location_id": ids["loc"], "business_date": TODAY,
                "occurred_at": T0 + timedelta(seconds=seq), "type": type_, "payload": payload,
            })

        taxed, owing = new_ulid(), new_ulid()
        ev(20, "order.placed", {
            "order_id": taxed, "order_number": 1010, "channel": "pos", "fulfillment": "dine_in",
            "lines": [{"id": new_ulid(), "quantity": 2, "name_snapshot": "Thali",
                       "unit_price_cents": 2200, "tax_rate_snapshot": 0.1}]})
        ev(21, "order.paid", {"order_id": taxed, "payment": {
            "id": new_ulid(), "provider": "cash", "amount_cents": 4400}})
        ev(22, "order.placed", {
            "order_id": owing, "order_number": 1011, "channel": "pos", "fulfillment": "dine_in",
            "lines": [{"id": new_ulid(), "quantity": 1, "name_snapshot": "Lassi",
                       "unit_price_cents": 650, "tax_rate_snapshot": 0.1}]})
        ev(23, "order.paid", {"order_id": owing, "payment": {
            "id": new_ulid(), "provider": "cash", "amount_cents": 200}})
        s.commit()

        report = shift_closeout(s, ids["loc"], TODAY)
        assert report["paid_sales_cents"] == 5400 + 4400
        assert report["gst_cents"] == 400  # 4400 / 11
        assert report["sales_ex_gst_cents"] == 5400 + 4000
        assert report["open_balance_cents"] == 450
        assert report["gross_sales_cents"] == 5400 + 4400 + 200  # money taken, part payments too
