"""Regression tests for the independent review of the self-host build.

Each test pins one finding: a till reaching into another location through sync, a stale
reference failing a whole batch, coerced numbers crashing apply, the seq counter moving
backwards, reused order numbers, and the kitchen ticket printing before its event.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from support import (
    CapturePrinter,
    Venue,
    api,
    auth,
    cmd,
    make_engine,
    order_body,
    seed_venue,
)
from void_pos.ids import new_ulid
from void_pos.menu import delete_item
from void_pos.models import Customer, Device, Event, Order, OrderLine, Payment


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, Venue, CapturePrinter, Session]]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


def _event(venue: Venue, *, seq: int, type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        **cmd(seq),
        "device_id": venue.device_id,
        "location_id": venue.location_id,
        "business_date": "2026-11-18",
        "occurred_at": "2026-11-18T10:00:00+10:00",
        "type": type,
        "payload": payload,
    }


def _placed_payload(venue: Venue, *, order_number: int, item_id: str | None = None) -> dict:
    body = order_body(venue, seq=1, order_number=order_number)
    if item_id is not None:
        body["lines"][0]["menu_item_id"] = item_id
    return {k: v for k, v in body.items() if k not in ("event_id", "seq")}


def _push(c: TestClient, venue: Venue, *events: dict[str, Any]) -> list[dict[str, Any]]:
    r = c.post("/sync/push", json={"events": list(events)}, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return r.json()["acks"]


def test_a_till_cannot_void_or_pay_another_locations_order(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till")

    void = _event(other, seq=1, type="order.voided", payload={"order_id": order["id"]})
    pay = _event(
        other,
        seq=2,
        type="order.paid",
        payload={
            "order_id": order["id"],
            "payment": {"id": new_ulid(), "provider": "cash", "amount_cents": 3700},
        },
    )
    acks = _push(c, other, void, pay)

    assert [(a["status"], a["code"]) for a in acks] == [
        ("rejected", "foreign_order"),
        ("rejected", "foreign_order"),
    ]
    assert c.get(f"/orders/{order['id']}", headers=auth(venue.token)).json()["state"] == "open"
    assert s.scalar(select(func.count()).select_from(Payment)) == 0


def test_an_order_from_a_stale_menu_still_lands_with_its_snapshot(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    delete_item(s, venue.item_id)  # the admin removes the dish; the till hasn't reloaded
    s.commit()

    stale = _event(venue, seq=1, type="order.placed", payload=_placed_payload(venue, order_number=1000))
    fine = _event(venue, seq=2, type="order.placed", payload=_placed_payload(venue, order_number=1001))
    acks = _push(c, venue, stale, fine)

    assert [a["status"] for a in acks] == ["applied", "applied"]
    line = s.scalars(
        select(OrderLine).where(OrderLine.order_id == stale["payload"]["order_id"])
    ).first()
    assert line is not None
    assert (line.menu_item_id, line.name_snapshot) == (None, "Masala Dosa")

    # The online path takes the same order too.
    r = c.post("/orders", json=order_body(venue, seq=3, order_number=1002), headers=auth(venue.token))
    assert r.status_code == 200, r.text


def test_another_locations_menu_item_is_not_linked(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till")
    event = _event(
        venue,
        seq=1,
        type="order.placed",
        payload=_placed_payload(venue, order_number=1000, item_id=other.item_id),
    )
    assert _push(c, venue, event)[0]["status"] == "applied"
    line = s.scalars(
        select(OrderLine).where(OrderLine.order_id == event["payload"]["order_id"])
    ).first()
    assert line is not None and line.menu_item_id is None


def test_numeric_strings_are_rejected_not_crashed_on(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    payload = _placed_payload(venue, order_number=1000)
    payload["order_number"] = "1000"
    bad_qty = _placed_payload(venue, order_number=1001)
    bad_qty["lines"][0]["quantity"] = "2"
    good = _placed_payload(venue, order_number=1002)

    acks = _push(
        c,
        venue,
        _event(venue, seq=1, type="order.placed", payload=payload),
        _event(venue, seq=2, type="order.placed", payload=bad_qty),
        _event(venue, seq=3, type="order.placed", payload=good),
    )
    assert [(a["status"], a["code"]) for a in acks] == [
        ("rejected", "invalid_payload"),
        ("rejected", "invalid_payload"),
        ("applied", None),
    ]


def test_loyalty_for_an_unknown_customer_is_rejected(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    entry = {"id": new_ulid(), "customer_id": new_ulid(), "points_delta": 10, "reason": "earn"}
    ack = _push(c, venue, _event(venue, seq=1, type="loyalty.recorded", payload=entry))[0]
    assert (ack["status"], ack["code"]) == ("rejected", "unknown_customer")

    customer = Customer(org_id=venue.org_id, location_id=venue.location_id, name="Asha")
    s.add(customer)
    s.commit()
    entry = {**entry, "id": new_ulid(), "customer_id": customer.id}
    assert _push(c, venue, _event(venue, seq=2, type="loyalty.recorded", payload=entry))[0][
        "status"
    ] == "applied"


def test_the_seq_counter_never_moves_backwards(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    later = _event(venue, seq=5, type="order.placed", payload=_placed_payload(venue, order_number=1000))
    earlier = _event(venue, seq=3, type="order.placed", payload=_placed_payload(venue, order_number=1001))
    _push(c, venue, later)
    _push(c, venue, earlier)
    device = s.get(Device, venue.device_id)
    assert device is not None
    s.refresh(device)
    assert device.last_seq == 5


def test_a_reused_order_number_is_refused_with_the_next_free_one(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    c.post("/orders", json=order_body(venue, seq=1, order_number=1000), headers=auth(venue.token))
    r = c.post("/orders", json=order_body(venue, seq=2, order_number=1000), headers=auth(venue.token))
    assert r.status_code == 409
    assert (r.json()["code"], r.json()["next_order_number"]) == ("order_number_used", 1001)
    assert s.scalar(select(func.count()).select_from(Order)) == 1


def test_a_reused_seq_on_kitchen_send_prints_nothing(ctx: Ctx) -> None:
    c, venue, printer, s = ctx
    order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()
    r = c.post(
        f"/orders/{order['id']}/send-to-kitchen",
        json={"event_id": new_ulid(), "seq": 1},
        headers=auth(venue.token),
    )
    assert (r.status_code, r.json()["code"], r.json()["last_seq"]) == (409, "seq_conflict", 1)
    assert printer.jobs == 0
    assert s.scalar(select(func.count()).select_from(Event)) == 1
