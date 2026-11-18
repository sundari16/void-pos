from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from support import (
    CapturePrinter,
    DownPrinter,
    Venue,
    api,
    auth,
    cmd,
    make_engine,
    order_body,
    seed_venue,
)
from void_pos.ids import new_ulid
from void_pos.models import Device, Event

Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


def _place(c: TestClient, venue: Venue, seq: int = 1) -> str:
    body = order_body(venue, seq=seq, order_number=999 + seq)
    placed = c.post("/orders", json=body, headers=auth(venue.token))
    assert placed.status_code == 200, placed.text
    return str(placed.json()["id"])


def _void(c: TestClient, venue: Venue, order_id: str, body: dict[str, Any]) -> Any:
    return c.post(f"/orders/{order_id}/void", json=body, headers=auth(venue.token))


def test_voiding_an_order_the_kitchen_has_prints_a_void_ticket(ctx: Ctx) -> None:
    c, venue, printer, s = ctx
    order_id = _place(c, venue)
    c.post(f"/orders/{order_id}/send-to-kitchen", json=cmd(2), headers=auth(venue.token))
    printer.sent.clear()

    body = {**cmd(3), "reason": "  Customer left  "}
    voided = _void(c, venue, order_id, body)
    assert voided.status_code == 200, voided.text
    assert voided.json() == {
        "order_id": order_id,
        "state": "voided",
        "printed": True,
        "replayed": False,
    }
    assert b"VOID #1000" in printer.sent and b"DO NOT MAKE" in printer.sent
    assert b"Reason: Customer left" in printer.sent

    # A retry replays without printing again.
    jobs = printer.jobs
    again = _void(c, venue, order_id, body)
    assert (again.json()["replayed"], again.json()["printed"], printer.jobs) == (True, False, jobs)

    event = s.scalars(select(Event).where(Event.type == "order.voided")).one()
    assert event.payload == {"order_id": order_id, "reason": "Customer left"}
    report = c.get(f"/reports/{venue.location_id}/closeout", headers=auth(venue.token)).json()
    assert report["orders"]["voided"] == 1


def test_an_order_not_yet_sent_voids_without_a_ticket(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    order_id = _place(c, venue)
    voided = _void(c, venue, order_id, {**cmd(2), "reason": "Rang up twice"})
    assert (voided.json()["state"], voided.json()["printed"]) == ("voided", False)
    assert printer.jobs == 0


def test_paid_and_voided_orders_cannot_be_voided(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    paid = _place(c, venue, seq=1)
    c.post(
        f"/orders/{paid}/pay",
        json={**cmd(2), "payment_id": new_ulid(), "method": "cash"},
        headers=auth(venue.token),
    )
    refused = _void(c, venue, paid, {**cmd(3), "reason": "Mistake"})
    assert (refused.status_code, refused.json()["code"]) == (409, "order_paid")

    voided = _place(c, venue, seq=3)
    assert _void(c, venue, voided, {**cmd(4), "reason": "Mistake"}).status_code == 200
    again = _void(c, venue, voided, {**cmd(5), "reason": "Mistake"})
    assert (again.status_code, again.json()["code"]) == (409, "order_voided")

    pay_voided = c.post(
        f"/orders/{voided}/pay",
        json={**cmd(5), "payment_id": new_ulid(), "method": "cash"},
        headers=auth(venue.token),
    )
    assert pay_voided.status_code == 409


def test_a_void_needs_a_reason(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order_id = _place(c, venue)
    for body in ({**cmd(2)}, {**cmd(2), "reason": "   "}, {**cmd(2), "reason": "x" * 201}):
        assert _void(c, venue, order_id, body).status_code == 422


def test_a_kitchen_printer_that_is_down_leaves_the_order_as_it_was() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, printer=CapturePrinter()) as c:
        order_id = _place(c, venue)
        c.post(f"/orders/{order_id}/send-to-kitchen", json=cmd(2), headers=auth(venue.token))
    with api(engine, printer=DownPrinter()) as c:
        body = {**cmd(3), "reason": "Customer left"}
        down = _void(c, venue, order_id, body)
        assert (down.status_code, down.json()["code"]) == (503, "printer_unavailable")
        assert c.get(f"/orders/{order_id}", headers=auth(venue.token)).json()["state"] == "sent"
        with Session(engine) as s:
            device = s.get(Device, venue.device_id)
            assert device is not None and device.last_seq == 2
    with api(engine, printer=CapturePrinter()) as c:
        assert _void(c, venue, order_id, body).json()["state"] == "voided"


def test_a_till_cannot_void_another_locations_order(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till-token-xyz")
    order_id = _place(c, venue)
    refused = c.post(
        f"/orders/{order_id}/void",
        json={**cmd(1), "reason": "Mistake"},
        headers=auth(other.token),
    )
    assert refused.status_code == 404


def test_a_synced_void_keeps_its_reason_and_older_tills_may_omit_it(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    first, second = _place(c, venue, seq=1), _place(c, venue, seq=2)

    def event(seq: int, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": new_ulid(),
            "device_id": venue.device_id,
            "seq": seq,
            "location_id": venue.location_id,
            "business_date": "2026-11-18",
            "occurred_at": "2026-11-18T08:00:00+10:00",
            "type": "order.voided",
            "payload": payload,
        }

    pushed = c.post(
        "/sync/push",
        json={"events": [
            event(3, {"order_id": first, "reason": "Spilled"}),
            event(4, {"order_id": second}),
        ]},
        headers=auth(venue.token),
    )
    assert [a["status"] for a in pushed.json()["acks"]] == ["applied", "applied"]
    reasons = {
        e.payload["order_id"]: e.payload.get("reason")
        for e in s.scalars(select(Event).where(Event.type == "order.voided"))
    }
    assert reasons == {first: "Spilled", second: None}
