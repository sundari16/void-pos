"""End-to-end HTTP flows a paired till and the venue admin actually run."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import (
    ADMIN_TOKEN,
    CapturePrinter,
    Venue,
    add_till,
    api,
    auth,
    cmd,
    make_engine,
    order_body,
    seed_venue,
)
from void_pos.clock import local_date, utcnow
from void_pos.ids import new_ulid


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, Venue, CapturePrinter, Session]]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


def test_place_send_pay_and_close_out(
    ctx: tuple[TestClient, Venue, CapturePrinter, Session],
) -> None:
    c, venue, printer, _ = ctx
    till = auth(venue.token)

    placed = c.post("/orders", json=order_body(venue, seq=1, business_date="2026-11-15"), headers=till)
    assert placed.status_code == 200, placed.text
    order = placed.json()
    assert (order["state"], order["total_cents"], order["replayed"]) == ("open", 3700, False)

    sent = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(2), headers=till)
    assert sent.status_code == 200, sent.text
    assert sent.json()["state"] == "sent"
    assert sent.json()["printed"] is True
    assert b"ORDER #1000" in printer.sent

    paid = c.post(
        f"/orders/{order['id']}/pay",
        json={**cmd(3), "payment_id": new_ulid(), "method": "cash"},
        headers=till,
    )
    assert paid.status_code == 200, paid.text
    assert (paid.json()["amount_cents"], paid.json()["order_state"]) == (3700, "paid")

    assert c.get(f"/orders/{order['id']}", headers=till).json()["state"] == "paid"

    report = c.get(
        f"/reports/{venue.location_id}/closeout",
        params={"business_date": "2026-11-15"},
        headers=till,
    )
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["gross_sales_cents"] == 3700
    assert body["payments_by_provider"]["cash"] == 3700
    assert body["orders"]["paid"] == 1


def test_business_date_defaults_to_the_locations_calendar(
    ctx: tuple[TestClient, Venue, CapturePrinter, Session],
) -> None:
    c, venue, _, _ = ctx
    before = local_date("Australia/Brisbane")
    placed = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token))
    after = local_date("Australia/Brisbane", utcnow() + timedelta(seconds=1))
    assert placed.json()["business_date"] in {before.isoformat(), after.isoformat()}


def test_no_printer_configured_still_marks_sent() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, printer=None) as c:
        order = c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()
        sent = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(2), headers=auth(venue.token))
    assert sent.status_code == 200, sent.text
    assert (sent.json()["state"], sent.json()["printed"]) == ("sent", False)


def test_health_and_readiness(ctx: tuple[TestClient, Venue, CapturePrinter, Session]) -> None:
    c, _, _, _ = ctx
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get("/health/ready").json() == {"status": "ok", "database": "ok"}


def test_tills_sync_through_the_server(
    ctx: tuple[TestClient, Venue, CapturePrinter, Session],
) -> None:
    c, venue, _, s = ctx
    add_till(s, venue.location_id, name="Till 2", token="till-2", order_range=(2000, 2999))
    s.commit()
    event = {
        **cmd(1),
        "device_id": venue.device_id,
        "location_id": venue.location_id,
        "business_date": "2026-11-15",
        "occurred_at": "2026-11-15T10:00:00+10:00",
        "type": "order.placed",
        "payload": {k: v for k, v in order_body(venue, seq=1).items() if k not in ("event_id", "seq")},
    }
    push = c.post("/sync/push", json={"events": [event]}, headers=auth(venue.token))
    assert push.status_code == 200, push.text
    assert push.json()["acks"][0]["status"] == "applied"

    other = c.get("/sync/pull", headers=auth("till-2"))
    assert [e["event_id"] for e in other.json()["events"]] == [event["event_id"]]

    own = c.get("/sync/pull", headers=auth(venue.token))
    assert own.json()["events"] == []
    own_too = c.get("/sync/pull", params={"include_own": "true"}, headers=auth(venue.token))
    assert len(own_too.json()["events"]) == 1


def test_export_is_admin_only(ctx: tuple[TestClient, Venue, CapturePrinter, Session]) -> None:
    c, venue, _, _ = ctx
    c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token))

    refused = c.get(f"/export/{venue.location_id}", headers=auth(venue.token))
    assert (refused.status_code, refused.json()["code"]) == (403, "admin_only")

    bundle = c.get(f"/export/{venue.location_id}", headers=auth(ADMIN_TOKEN))
    assert bundle.status_code == 200, bundle.text
    assert len(bundle.json()["orders"]) == 1

    csv_resp = c.get(f"/export/{venue.location_id}/csv/orders", headers=auth(ADMIN_TOKEN))
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "order_number" in csv_resp.text
    unknown = c.get(f"/export/{venue.location_id}/csv/bogus", headers=auth(ADMIN_TOKEN))
    assert (unknown.status_code, unknown.json()["code"]) == (404, "unknown_entity")


def test_menu_is_read_by_tills_and_edited_by_the_admin(
    ctx: tuple[TestClient, Venue, CapturePrinter, Session],
) -> None:
    c, venue, _, _ = ctx
    admin, till = auth(ADMIN_TOKEN), auth(venue.token)
    items_url = f"/locations/{venue.location_id}/menu/items"

    refused = c.post(items_url, json={"name": "Chai", "base_price_cents": 400}, headers=till)
    assert refused.status_code == 403

    created = c.post(
        items_url, json={"name": "Chai", "base_price_cents": 400, "category": "Drinks"}, headers=admin
    )
    assert created.status_code == 201, created.text
    item_id = created.json()["id"]
    assert any(i["id"] == item_id for i in c.get(items_url, headers=till).json())

    patched = c.patch(f"/menu/items/{item_id}", json={"base_price_cents": 450}, headers=admin)
    assert patched.json()["base_price_cents"] == 450
    cleared = c.patch(f"/menu/items/{item_id}", json={"name": None}, headers=admin)
    assert (cleared.status_code, cleared.json()["code"]) == (422, "invalid_request")

    assert c.delete(f"/menu/items/{item_id}", headers=admin).status_code == 204
    gone = c.get(f"/menu/items/{item_id}", headers=till)
    assert (gone.status_code, gone.json()["code"]) == (404, "not_found")


def test_menu_validation(ctx: tuple[TestClient, Venue, CapturePrinter, Session]) -> None:
    c, venue, _, _ = ctx
    admin = auth(ADMIN_TOKEN)
    items_url = f"/locations/{venue.location_id}/menu/items"
    negative = c.post(items_url, json={"name": "X", "base_price_cents": -1}, headers=admin)
    assert negative.status_code == 422
    blank = c.post(items_url, json={"name": "", "base_price_cents": 100}, headers=admin)
    assert blank.status_code == 422
    nowhere = c.post(
        "/locations/00000000000000000000000000/menu/items",
        json={"name": "X", "base_price_cents": 1},
        headers=admin,
    )
    assert nowhere.status_code == 404
    groups = f"/locations/{venue.location_id}/menu/modifier-groups"
    upside_down = c.post(groups, json={"name": "Spice", "min_select": 3, "max_select": 1}, headers=admin)
    assert (upside_down.status_code, upside_down.json()["code"]) == (422, "invalid_selection_limits")
