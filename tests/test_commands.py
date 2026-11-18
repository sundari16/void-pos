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


def test_a_bill_split_two_ways_over_the_api(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    url = f"/orders/{order['id']}/pay"
    till = auth(venue.token)

    half = c.post(url, json={**cmd(2), "payment_id": new_ulid(), "amount_cents": 1850}, headers=till)
    assert half.status_code == 200, half.text
    assert (half.json()["order_state"], half.json()["balance_cents"]) == ("open", 1850)
    read = c.get(f"/orders/{order['id']}", headers=till).json()
    assert (read["total_cents"], read["balance_cents"]) == (3700, 1850)

    void = c.post(f"/orders/{order['id']}/void", json={**cmd(3), "reason": "Left"}, headers=till)
    assert (void.status_code, void.json()["code"]) == (409, "order_paid")

    over = c.post(url, json={**cmd(3), "payment_id": new_ulid(), "amount_cents": 2000}, headers=till)
    assert (over.status_code, over.json()["code"], over.json()["balance_cents"]) == (
        422,
        "amount_exceeds_balance",
        1850,
    )
    rest = c.post(
        url,
        json={**cmd(3), "payment_id": new_ulid(), "method": "external", "external_ref": "4471"},
        headers=till,
    )
    assert (rest.json()["amount_cents"], rest.json()["order_state"], rest.json()["balance_cents"]) == (
        1850,
        "paid",
        0,
    )
    report = c.get(f"/reports/{venue.location_id}/closeout", headers=till).json()
    assert report["payments_by_provider"] == {"cash": 1850, "external": 1850}


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


def test_dine_in_lines_carry_seat_and_course(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    body = order_body(venue, seq=1)
    body["fulfillment"] = "dine_in"
    body["lines"][0].update(seat_number=2, course_number=1)
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 200, r.text

    from void_pos.models import OrderLine

    item = s.get(OrderLine, body["lines"][0]["id"])
    modifier = s.get(OrderLine, body["lines"][1]["id"])
    assert item is not None and modifier is not None
    assert (item.seat_number, item.course_number) == (2, 1)
    assert (modifier.seat_number, modifier.course_number) == (None, None)


@pytest.mark.parametrize(
    ("line", "change", "fragment"),
    [
        (1, {"seat_number": 2}, "can't have its own seat or course"),
        (0, {"seat_number": 0}, "seat_number"),
        (0, {"course_number": 21}, "course_number"),
        (0, {"seat_number": "2"}, "seat_number"),
    ],
)
def test_seat_and_course_are_validated(
    ctx: Ctx, line: int, change: dict, fragment: str
) -> None:
    c, venue, _, _ = ctx
    body = order_body(venue, seq=1)
    body["lines"][line].update(change)
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 422, r.text
    assert fragment in r.json()["detail"]


def test_seat_and_course_are_in_the_export(ctx: Ctx) -> None:
    from support import ADMIN_TOKEN

    c, venue, _, _ = ctx
    body = order_body(venue, seq=1)
    body["lines"][0].update(seat_number=3, course_number=2)
    c.post("/orders", json=body, headers=auth(venue.token))
    csv_text = c.get(
        f"/export/{venue.location_id}/csv/order_lines", headers=auth(ADMIN_TOKEN)
    ).text
    header, *rows = csv_text.strip().splitlines()
    assert header.endswith("category,seat_number,course_number")
    assert rows[0].endswith("Mains,3,2")
