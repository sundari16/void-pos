"""Regressions from the review of the showcase build."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import CapturePrinter, Venue, api, auth, cmd, make_engine, order_body, seed_venue
from void_pos.ids import new_ulid
from void_pos.staff import create_staff

Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


def _post(c: TestClient, venue: Venue, path: str, body: dict[str, Any]) -> Any:
    return c.post(path, json=body, headers=auth(venue.token))


def _place(c: TestClient, venue: Venue) -> dict[str, Any]:
    return dict(_post(c, venue, "/orders", order_body(venue, seq=1)).json())


def test_an_item_taken_off_before_sending_never_reaches_the_kitchen(ctx: Ctx) -> None:
    c, venue, printer, s = ctx
    order = _place(c, venue)
    _post(c, venue, f"/orders/{order['id']}/send-to-kitchen", cmd(2))
    naan = {"id": new_ulid(), "name_snapshot": "Garlic Naan", "unit_price_cents": 450}
    _post(c, venue, f"/orders/{order['id']}/lines", {**cmd(3), "lines": [naan]})
    _post(c, venue, f"/orders/{order['id']}/remove-lines",
          {**cmd(4), "line_ids": [naan["id"]], "reason": "Changed mind"})
    printer.sent.clear()
    sent = _post(c, venue, f"/orders/{order['id']}/send-to-kitchen", cmd(5)).json()
    assert sent["lines_sent"] == 0
    assert b"REPRINT #1000" in printer.sent and b"Garlic Naan" not in printer.sent

    # A dish the kitchen had and then cancelled isn't on the VOID ticket either.
    create_staff(s, location_id=venue.location_id, name="Asha", role="manager", pin="4821")
    s.commit()
    more = {"id": new_ulid(), "name_snapshot": "Mango Lassi", "unit_price_cents": 650}
    _post(c, venue, f"/orders/{order['id']}/lines", {**cmd(6), "lines": [more]})
    _post(c, venue, f"/orders/{order['id']}/send-to-kitchen", cmd(7))
    _post(c, venue, f"/orders/{order['id']}/remove-lines",
          {**cmd(8), "line_ids": [more["id"]], "reason": "x", "manager_pin": "4821"})
    printer.sent.clear()
    _post(c, venue, f"/orders/{order['id']}/void",
          {**cmd(9), "reason": "Left", "manager_pin": "4821"})
    assert b"VOID #1000" in printer.sent and b"Mango Lassi" not in printer.sent


def test_an_order_cut_to_what_is_paid_is_paid(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)  # 3700
    _post(c, venue, f"/orders/{order['id']}/pay",
          {**cmd(2), "payment_id": new_ulid(), "amount_cents": 2000})
    surcharge = _post(c, venue, f"/orders/{order['id']}/adjustments", {
        **cmd(3), "id": new_ulid(), "kind": "surcharge", "basis": "amount", "value": 100})
    assert surcharge.json()["state"] == "open"
    discount = _post(c, venue, f"/orders/{order['id']}/adjustments", {
        **cmd(4), "id": new_ulid(), "kind": "discount", "basis": "amount", "value": 1800})
    assert discount.status_code == 200, discount.text
    assert (discount.json()["state"], discount.json()["balance_cents"]) == ("paid", 0)


def test_a_free_order_with_nothing_paid_stays_open(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    gone = _post(c, venue, f"/orders/{order['id']}/remove-lines", {
        **cmd(2), "line_ids": [order["lines"][0]["id"]], "reason": "x"})
    assert (gone.json()["state"], gone.json()["total_cents"]) == ("open", 0)


def _event(venue: Venue, seq: int, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": new_ulid(), "device_id": venue.device_id, "seq": seq,
        "location_id": venue.location_id, "business_date": "2026-11-19",
        "occurred_at": f"2026-11-19T12:00:{seq:02d}+10:00", "type": type_, "payload": payload,
    }


def test_synced_refunds_follow_the_online_rules(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    part = new_ulid()
    _post(c, venue, f"/orders/{order['id']}/pay",
          {**cmd(2), "payment_id": part, "amount_cents": 2000})

    def refund(seq: int, payment_id: str, provider: str) -> dict[str, Any]:
        payload = {"order_id": order["id"], "refund": {
            "id": new_ulid(), "payment_id": payment_id, "amount_cents": 500,
            "reason": "x", "provider": provider}}
        r = c.post("/sync/push", json={"events": [_event(venue, seq, "order.refunded", payload)]},
                   headers=auth(venue.token))
        return dict(r.json()["acks"][0])

    early = refund(3, part, "cash")
    assert (early["status"], early["code"]) == ("rejected", "order_not_paid")
    _post(c, venue, f"/orders/{order['id']}/pay", {**cmd(3), "payment_id": new_ulid()})
    wrong = refund(4, part, "stripe")
    assert (wrong["status"], wrong["code"]) == ("rejected", "wrong_refund_method")
    assert refund(4, part, "cash")["status"] == "applied"


def test_a_refunded_order_takes_no_more_items(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    payment = new_ulid()
    _post(c, venue, f"/orders/{order['id']}/pay", {**cmd(2), "payment_id": payment})
    back = _post(c, venue, f"/orders/{order['id']}/refunds", {
        **cmd(3), "refund_id": new_ulid(), "payment_id": payment, "amount_cents": 3700,
        "reason": "Cold"})
    assert back.json()["state"] == "refunded"
    more = _post(c, venue, f"/orders/{order['id']}/lines", {
        **cmd(4), "lines": [{"id": new_ulid(), "name_snapshot": "Chai", "unit_price_cents": 400}]})
    assert (more.status_code, more.json()["code"]) == (409, "order_closed")
    listed = c.get(f"/locations/{venue.location_id}/orders", params={"state": "refunded"},
                   headers=auth(venue.token))
    assert [o["id"] for o in listed.json()] == [order["id"]]
