from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
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
from void_pos.menu import create_item, update_item
from void_pos.models import OrderLine

Ctx = tuple[TestClient, Venue, CapturePrinter, Session, str]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        naan = create_item(
            s, location_id=venue.location_id, name="Garlic Naan", base_price_cents=450
        ).id
        s.commit()
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s, naan


def _naan(naan_id: str, qty: int = 2) -> list[dict[str, Any]]:
    item = new_ulid()
    return [
        {"id": item, "menu_item_id": naan_id, "quantity": qty, "name_snapshot": "Garlic Naan",
         "unit_price_cents": 450},
        {"id": new_ulid(), "parent_line_id": item, "name_snapshot": "Extra butter",
         "unit_price_cents": 100},
    ]


def _add(c: TestClient, venue: Venue, order_id: str, body: dict[str, Any]) -> Any:
    return c.post(f"/orders/{order_id}/lines", json=body, headers=auth(venue.token))


def test_a_table_orders_more_and_the_kitchen_gets_just_that(ctx: Ctx) -> None:
    c, venue, printer, _, naan = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1, table_label="12"), headers=till).json()
    first = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(2), headers=till).json()
    assert (first["lines_sent"], first["state"]) == (2, "sent")
    assert b"ORDER #1000" in printer.sent and b"Table 12" in printer.sent
    printer.sent.clear()

    body = {**cmd(3), "lines": _naan(naan)}
    added = _add(c, venue, order["id"], body)
    assert added.status_code == 200, added.text
    after = added.json()
    assert (after["state"], after["total_cents"], after["balance_cents"]) == ("sent", 4800, 4800)
    assert [(line["name"], line["sent"]) for line in after["lines"]] == [
        ("Masala Dosa", True),
        ("Extra hot", True),
        ("Garlic Naan", False),
        ("Extra butter", False),
    ]
    assert after["unsent_lines"] == 2

    replay = _add(c, venue, order["id"], body).json()
    assert replay["replayed"] is True and len(replay["lines"]) == 4

    more = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(4), headers=till).json()
    assert (more["lines_sent"], more["state"]) == (2, "sent")
    assert b"ADDED #1000" in printer.sent and b"Garlic Naan" in printer.sent
    assert b"Extra hot" not in printer.sent  # the dosa (printed by its Tamil name) isn't resent
    printer.sent.clear()

    again = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(5), headers=till).json()
    assert again["lines_sent"] == 0
    assert b"REPRINT #1000" in printer.sent and b"Extra hot" in printer.sent
    listed = c.get(f"/locations/{venue.location_id}/orders", headers=till).json()
    assert listed[0]["unsent_lines"] == 0


def test_closed_orders_and_reused_ids_are_refused(ctx: Ctx) -> None:
    c, venue, _, s, naan = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    taken = _add(c, venue, order["id"], {**cmd(2), "lines": [{**_naan(naan)[0], "id": order["lines"][0]["id"]}]})
    assert (taken.status_code, taken.json()["code"]) == (409, "line_exists")

    update_item(s, naan, {"is_available": False})
    s.commit()
    off = _add(c, venue, order["id"], {**cmd(2), "lines": _naan(naan)})
    assert (off.status_code, off.json()["code"]) == (409, "item_unavailable")
    update_item(s, naan, {"is_available": True})
    s.commit()

    c.post(f"/orders/{order['id']}/pay", json={**cmd(2), "payment_id": new_ulid()}, headers=till)
    paid = _add(c, venue, order["id"], {**cmd(3), "lines": _naan(naan)})
    assert (paid.status_code, paid.json()["code"]) == (409, "order_closed")

    other = c.post("/orders", json=order_body(venue, seq=3, order_number=1001), headers=till).json()
    c.post(f"/orders/{other['id']}/void", json={**cmd(4), "reason": "Left"}, headers=till)
    voided = _add(c, venue, other["id"], {**cmd(5), "lines": _naan(naan)})
    assert (voided.status_code, voided.json()["code"]) == (409, "order_voided")

    lonely = _add(c, venue, order["id"], {**cmd(5), "lines": [_naan(naan)[1]]})
    assert lonely.status_code == 422  # a modifier needs its item in the same batch


def test_items_added_to_a_part_paid_bill_raise_the_balance(ctx: Ctx) -> None:
    c, venue, _, _, naan = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    c.post(
        f"/orders/{order['id']}/pay",
        json={**cmd(2), "payment_id": new_ulid(), "amount_cents": 3700 - 100},
        headers=till,
    )
    after = _add(c, venue, order["id"], {**cmd(3), "lines": _naan(naan, qty=1)}).json()
    assert (after["state"], after["balance_cents"]) == ("open", 100 + 550)


def _event(venue: Venue, seq: int, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": new_ulid(),
        "device_id": venue.device_id,
        "seq": seq,
        "location_id": venue.location_id,
        "business_date": "2026-11-18",
        "occurred_at": f"2026-11-18T08:00:{seq:02d}+10:00",
        "type": type_,
        "payload": payload,
    }


def test_synced_additions_and_older_sends(ctx: Ctx) -> None:
    c, venue, _, s, naan = ctx
    placed = {k: v for k, v in order_body(venue, seq=1).items() if k not in ("event_id", "seq")}
    order_id = placed["order_id"]
    extra = _naan(naan)
    events = [
        _event(venue, 1, "order.placed", placed),
        _event(venue, 2, "order.sent", {"order_id": order_id}),  # an older till: no line_ids
        _event(venue, 3, "order.lines_added", {"order_id": order_id, "lines": extra}),
        _event(venue, 4, "order.sent", {"order_id": order_id, "line_ids": [extra[0]["id"]]}),
    ]
    acks = c.post("/sync/push", json={"events": events}, headers=auth(venue.token)).json()["acks"]
    assert [a["status"] for a in acks] == ["applied"] * 4
    sent = {
        line.name_snapshot: line.sent_at is not None
        for line in s.scalars(select(OrderLine).where(OrderLine.order_id == order_id))
    }
    assert sent == {"Masala Dosa": True, "Extra hot": True, "Garlic Naan": True, "Extra butter": False}

    again = c.post(
        "/sync/push",
        json={"events": [_event(venue, 5, "order.lines_added", {"order_id": order_id, "lines": extra})]},
        headers=auth(venue.token),
    ).json()["acks"][0]
    assert (again["status"], again["code"]) == ("rejected", "line_exists")


def test_counter_service_sends_after_paying(ctx: Ctx) -> None:
    c, venue, printer, _, _ = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    c.post(f"/orders/{order['id']}/pay", json={**cmd(2), "payment_id": new_ulid()}, headers=till)
    sent = c.post(f"/orders/{order['id']}/send-to-kitchen", json=cmd(3), headers=till).json()
    assert (sent["state"], sent["lines_sent"]) == ("paid", 2)
    assert b"ORDER #1000" in printer.sent
    read = c.get(f"/orders/{order['id']}", headers=till).json()
    assert all(line["sent"] for line in read["lines"])
