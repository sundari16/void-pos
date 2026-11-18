"""Till commands are idempotent, validated, and fail with a clear error and no side effect."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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
from void_pos.models import Event, Order, Payment


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, Venue, CapturePrinter, Session]]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


def _place(c: TestClient, venue: Venue, seq: int = 1, **kw: object) -> dict:
    r = c.post("/orders", json=order_body(venue, seq=seq, **kw), headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return r.json()


def test_a_retried_order_replays_instead_of_duplicating(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    body = order_body(venue, seq=1)
    first = c.post("/orders", json=body, headers=auth(venue.token))
    again = c.post("/orders", json=body, headers=auth(venue.token))

    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert (first.json()["replayed"], again.json()["replayed"]) == (False, True)
    assert s.scalar(select(func.count()).select_from(Order)) == 1
    assert s.scalar(select(func.count()).select_from(Event)) == 1


def test_an_event_id_reused_for_another_order_is_refused(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    body = order_body(venue, seq=1)
    c.post("/orders", json=body, headers=auth(venue.token))
    clash = order_body(venue, seq=2, order_number=1001, event_id=body["event_id"])
    r = c.post("/orders", json=clash, headers=auth(venue.token))
    assert (r.status_code, r.json()["code"]) == (409, "event_id_reused")


def test_a_reused_seq_tells_the_till_where_the_server_is(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    _place(c, venue, seq=1)
    r = c.post("/orders", json=order_body(venue, seq=1, order_number=1001), headers=auth(venue.token))
    assert r.status_code == 409
    assert (r.json()["code"], r.json()["last_seq"]) == ("seq_conflict", 1)


def test_order_number_outside_the_tills_range(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    r = c.post("/orders", json=order_body(venue, seq=1, order_number=5000), headers=auth(venue.token))
    assert (r.status_code, r.json()["code"]) == (422, "order_number_out_of_range")


def test_an_existing_order_id_is_refused(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue, seq=1)
    r = c.post(
        "/orders", json=order_body(venue, seq=2, order_id=order["id"]), headers=auth(venue.token)
    )
    assert (r.status_code, r.json()["code"]) == (409, "order_exists")


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        (lambda b: b.update(lines=[]), "lines"),
        (lambda b: b["lines"][0].update(unit_price_cents=-5), "negative price"),
        (lambda b: b["lines"][0].update(quantity=0), "quantity"),
        (lambda b: b["lines"][1].update(parent_line_id=new_ulid()), "must point at an item"),
        (lambda b: b.update(order_id="not-a-ulid"), "order_id"),
        (lambda b: b.update(channel="fax"), "channel"),
        (lambda b: b.update(seq=0), "seq"),
        (lambda b: b.update(occurred_at="2026-11-15T10:00:00"), "occurred_at"),
    ],
)
def test_malformed_orders_are_refused_with_a_readable_reason(
    ctx: Ctx, change: object, fragment: str
) -> None:
    c, venue, _, s = ctx
    body = order_body(venue, seq=1)
    change(body)  # type: ignore[operator]
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "invalid_request"
    assert fragment in r.json()["detail"]
    assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_kitchen_send_replays_without_reprinting(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    order = _place(c, venue)
    command = cmd(2)
    first = c.post(f"/orders/{order['id']}/send-to-kitchen", json=command, headers=auth(venue.token))
    again = c.post(f"/orders/{order['id']}/send-to-kitchen", json=command, headers=auth(venue.token))
    assert (first.json()["printed"], again.json()["replayed"]) == (True, True)
    assert printer.jobs == 1


def test_sending_again_reprints(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    order = _place(c, venue)
    c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(2), headers=auth(venue.token))
    c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(3), headers=auth(venue.token))
    assert printer.jobs == 2


def test_a_paid_counter_order_still_prints(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    order = _place(c, venue)
    pay = {**cmd(2), "payment_id": new_ulid()}
    c.post(f"/orders/{order['id']}/pay", json=pay, headers=auth(venue.token))
    sent = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(3), headers=auth(venue.token))
    assert sent.status_code == 200, sent.text
    assert (sent.json()["state"], printer.jobs) == ("paid", 1)


def test_a_voided_order_never_reaches_the_kitchen(ctx: Ctx) -> None:
    c, venue, printer, _ = ctx
    order = _place(c, venue)
    void = {
        **cmd(2),
        "device_id": venue.device_id,
        "location_id": venue.location_id,
        "business_date": "2026-11-15",
        "occurred_at": "2026-11-15T10:00:00+00:00",
        "type": "order.voided",
        "payload": {"order_id": order["id"]},
    }
    assert c.post("/sync/push", json={"events": [void]}, headers=auth(venue.token)).status_code == 200
    r = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(3), headers=auth(venue.token))
    assert (r.status_code, r.json()["code"]) == (409, "order_voided")
    assert printer.jobs == 0


def test_an_unreachable_printer_leaves_the_order_open_for_a_retry() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, printer=DownPrinter()) as c:
        order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()
        command = cmd(2)
        down = c.post(f"/orders/{order['id']}/send-to-kitchen", json=command, headers=auth(venue.token))
        assert (down.status_code, down.json()["code"]) == (503, "printer_unavailable")
        assert c.get(f"/orders/{order['id']}", headers=auth(venue.token)).json()["state"] == "open"

    printer = CapturePrinter()
    with api(engine, printer=printer) as c:
        retry = c.post(f"/orders/{order['id']}/send-to-kitchen", json=command, headers=auth(venue.token))
        assert retry.status_code == 200, retry.text
        assert (retry.json()["state"], retry.json()["replayed"], printer.jobs) == ("sent", False, 1)


def test_a_retried_payment_replays(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = _place(c, venue)
    pay = {**cmd(2), "payment_id": new_ulid()}
    first = c.post(f"/orders/{order['id']}/pay", json=pay, headers=auth(venue.token))
    again = c.post(f"/orders/{order['id']}/pay", json=pay, headers=auth(venue.token))
    assert again.status_code == 200, again.text
    assert again.json()["payment_id"] == first.json()["payment_id"] == pay["payment_id"]
    assert again.json()["replayed"] is True
    assert s.scalar(select(func.count()).select_from(Payment)) == 1


def test_paying_twice_is_refused(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    c.post(f"/orders/{order['id']}/pay", json={**cmd(2), "payment_id": new_ulid()}, headers=auth(venue.token))
    r = c.post(f"/orders/{order['id']}/pay", json={**cmd(3), "payment_id": new_ulid()}, headers=auth(venue.token))
    assert (r.status_code, r.json()["code"], r.json()["order_state"]) == (409, "order_closed", "paid")


def test_underpaying_is_refused_with_the_total(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    r = c.post(
        f"/orders/{order['id']}/pay",
        json={**cmd(2), "payment_id": new_ulid(), "amount_cents": 1000},
        headers=auth(venue.token),
    )
    assert (r.status_code, r.json()["code"], r.json()["total_cents"]) == (422, "amount_mismatch", 3700)


def test_external_payments_need_a_reference(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    url = f"/orders/{order['id']}/pay"
    missing = c.post(url, json={**cmd(2), "payment_id": new_ulid(), "method": "external"}, headers=auth(venue.token))
    assert missing.status_code == 422
    assert "external_ref" in missing.json()["detail"]
    ok = c.post(
        url,
        json={**cmd(2), "payment_id": new_ulid(), "method": "external", "external_ref": "pi_test_1"},
        headers=auth(venue.token),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["provider"] == "external"


def test_unknown_and_foreign_orders_are_not_found(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = _place(c, venue)
    missing = c.post("/orders/00000000000000000000000000/send-to-kitchen", json=cmd(2), headers=auth(venue.token))
    assert (missing.status_code, missing.json()["code"]) == (404, "not_found")

    seed_venue(s, location_name="Gold Coast", token="gold-coast-till")
    foreign = c.get(f"/orders/{order['id']}", headers=auth("gold-coast-till"))
    assert foreign.status_code == 404
