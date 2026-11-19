from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from support import (
    ADMIN_TOKEN,
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
from void_pos.models import Event
from void_pos.staff import create_staff
from void_pos.totals import order_math

Ctx = tuple[TestClient, Venue, CapturePrinter, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    printer = CapturePrinter()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine, printer=printer) as client:
            yield client, venue, printer, s


def _line(qty: int, price: int, rate: str = "0.1") -> Any:
    return SimpleNamespace(
        id=new_ulid(), parent_line_id=None, quantity=qty, unit_price_cents=price,
        tax_rate_snapshot=Decimal(rate),
    )


def _adj(kind: str, basis: str, value: int) -> Any:
    return SimpleNamespace(id=new_ulid(), kind=kind, basis=basis, value=value)


def test_discounts_then_surcharges_and_gst_follows() -> None:
    lines = [_line(2, 1100), _line(1, 1000, "0")]  # 2200 at 10% GST, 1000 GST-free
    plain = order_math(lines)
    assert (plain.subtotal_cents, plain.total_cents, plain.gst_cents) == (3200, 3200, 200)

    ten_off, holiday = _adj("discount", "percent", 1000), _adj("surcharge", "percent", 1500)
    money = order_math(lines, [ten_off, holiday])
    assert money.adjustments == {ten_off.id: -320, holiday.id: 432}  # 15% of 2880
    assert money.total_cents == 3312
    # GST rides on the taxable share of the final total: 2200 / 3200 of 3312 = 2277, / 11.
    assert money.gst_cents == 207

    too_much = _adj("discount", "amount", 99_999)
    clamped = order_math(lines, [too_much])
    assert (clamped.adjustments[too_much.id], clamped.total_cents, clamped.gst_cents) == (-3200, 0, 0)


def _place(c: TestClient, venue: Venue, seq: int = 1, **extra: Any) -> dict[str, Any]:
    body = order_body(venue, seq=seq, order_number=999 + seq, **extra)
    body["lines"][0]["tax_rate_snapshot"] = 0.1
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return dict(r.json())


def _manager(s: Session, venue: Venue) -> str:
    create_staff(s, location_id=venue.location_id, name="Asha", role="manager", pin="4821")
    s.commit()
    return "4821"


def _post(c: TestClient, venue: Venue, path: str, body: dict[str, Any]) -> Any:
    return c.post(path, json=body, headers=auth(venue.token))


def test_taking_items_off_an_open_order(ctx: Ctx) -> None:
    c, venue, printer, s = ctx
    order = _place(c, venue)
    extra = [{"id": new_ulid(), "name_snapshot": "Chai", "unit_price_cents": 400}]
    order = _post(c, venue, f"/orders/{order['id']}/lines", {**cmd(2), "lines": extra}).json()
    chai, dosa = order["lines"][2]["id"], order["lines"][0]["id"]
    pin = _manager(s, venue)

    # Not sent yet: off it comes, no PIN, nothing printed.
    r = _post(c, venue, f"/orders/{order['id']}/remove-lines",
              {**cmd(3), "line_ids": [chai], "reason": "Changed mind"})
    assert r.status_code == 200, r.text
    assert (r.json()["total_cents"], r.json()["lines"][2]["removed"]) == (3700, True)
    assert printer.jobs == 0

    _post(c, venue, f"/orders/{order['id']}/send-to-kitchen", cmd(4))
    printer.sent.clear()
    body = {**cmd(5), "line_ids": [dosa], "reason": "Burnt"}
    needs = _post(c, venue, f"/orders/{order['id']}/remove-lines", body)
    assert (needs.status_code, needs.json()["code"]) == (403, "manager_pin_required")
    done = _post(c, venue, f"/orders/{order['id']}/remove-lines", {**body, "manager_pin": pin})
    assert done.status_code == 200, done.text
    assert done.json()["total_cents"] == 0
    assert b"CANCEL #1000" in printer.sent and b"Extra hot" in printer.sent
    removed = s.scalars(select(Event).where(Event.type == "order.lines_removed")).all()
    assert removed[-1].payload["approved_by_name"] == "Asha"

    again = _post(c, venue, f"/orders/{order['id']}/remove-lines",
                  {**cmd(6), "line_ids": [dosa], "reason": "x", "manager_pin": pin})
    assert (again.status_code, again.json()["code"]) == (422, "unknown_line")


def test_items_cant_come_off_below_what_is_paid(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    _post(c, venue, f"/orders/{order['id']}/pay",
          {**cmd(2), "payment_id": new_ulid(), "amount_cents": 2000})
    r = _post(c, venue, f"/orders/{order['id']}/remove-lines",
              {**cmd(3), "line_ids": [order["lines"][0]["id"]], "reason": "x"})
    assert (r.status_code, r.json()["code"]) == (409, "below_paid")


def _adjust(c: TestClient, venue: Venue, order_id: str, seq: int, **body: Any) -> Any:
    return _post(c, venue, f"/orders/{order_id}/adjustments", {**cmd(seq), "id": new_ulid(), **body})


def test_discounts_need_a_manager_and_surcharges_dont(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = _place(c, venue)
    pin = _manager(s, venue)
    disc = {"kind": "discount", "basis": "percent", "value": 1000, "label": "Staff meal"}
    assert _adjust(c, venue, order["id"], 2, **disc).json()["code"] == "manager_pin_required"
    ok = _adjust(c, venue, order["id"], 2, **disc, manager_pin=pin)
    assert ok.status_code == 200, ok.text
    assert (ok.json()["subtotal_cents"], ok.json()["total_cents"]) == (3700, 3330)
    [adj] = ok.json()["adjustments"]
    assert (adj["label"], adj["amount_cents"]) == ("Staff meal", -370)

    holiday = _adjust(c, venue, order["id"], 3, kind="surcharge", basis="percent", value=1500)
    assert holiday.json()["total_cents"] == 3330 + 500  # 15% of 3330, half up
    gone = _post(c, venue, f"/orders/{order['id']}/adjustments/{adj['id']}/remove", cmd(4))
    assert gone.json()["total_cents"] == 3700 + 555

    big = _adjust(c, venue, order["id"], 5, kind="discount", basis="amount", value=999_999,
                  manager_pin=pin)
    assert (big.status_code, big.json()["code"]) == (422, "discount_too_big")
    assert _adjust(c, venue, order["id"], 5, kind="discount", basis="percent", value=10_001,
                   manager_pin=pin).status_code == 422


def test_tips_ride_on_payments_not_sales(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order = _place(c, venue)
    paid = _post(c, venue, f"/orders/{order['id']}/pay",
                 {**cmd(2), "payment_id": new_ulid(), "tip_cents": 500})
    assert (paid.json()["amount_cents"], paid.json()["tip_cents"], paid.json()["order_state"]) == (
        3700, 500, "paid",
    )
    report = c.get(f"/reports/{venue.location_id}/closeout", headers=auth(venue.token)).json()
    assert (report["gross_sales_cents"], report["tips_cents"]) == (3700, 500)
    assert report["tips_by_provider"] == {"cash": 500}
    receipt = c.get(f"/orders/{order['id']}/receipt", headers=auth(venue.token)).json()
    assert {"label": "Tip (Cash)", "amount_cents": 500, "reference": None} in receipt["payments"]


def _refund(c: TestClient, venue: Venue, order_id: str, seq: int, payment_id: str, **body: Any) -> Any:
    return _post(c, venue, f"/orders/{order_id}/refunds", {
        **cmd(seq), "refund_id": new_ulid(), "payment_id": payment_id, "reason": "Cold food",
        **body,
    })


def test_refunds_go_back_through_the_payments_own_way(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = _place(c, venue)
    cash, card = new_ulid(), new_ulid()
    _post(c, venue, f"/orders/{order['id']}/pay", {**cmd(2), "payment_id": cash, "amount_cents": 2000})
    early = _refund(c, venue, order["id"], 3, cash, amount_cents=100)
    assert (early.status_code, early.json()["code"]) == (409, "order_not_paid")
    _post(c, venue, f"/orders/{order['id']}/pay", {
        **cmd(3), "payment_id": card, "method": "external", "external_ref": "APPR 7",
        "tip_cents": 300,
    })
    pin = _manager(s, venue)

    assert _refund(c, venue, order["id"], 4, cash, amount_cents=500).json()["code"] == "manager_pin_required"
    part = _refund(c, venue, order["id"], 4, cash, amount_cents=500, manager_pin=pin)
    assert part.status_code == 200, part.text
    assert (part.json()["state"], part.json()["refunded_cents"]) == ("paid", 500)
    over = _refund(c, venue, order["id"], 5, cash, amount_cents=1501, manager_pin=pin)
    assert (over.status_code, over.json()["code"]) == (422, "refund_exceeds_payment")
    no_ref = _refund(c, venue, order["id"], 5, card, amount_cents=100, manager_pin=pin)
    assert no_ref.status_code == 402  # the terminal's refund reference is needed

    rest_cash = _refund(c, venue, order["id"], 5, cash, amount_cents=1500, manager_pin=pin)
    assert rest_cash.status_code == 200
    whole = _refund(c, venue, order["id"], 6, card, amount_cents=1700 + 300,
                    external_ref="REF 99", manager_pin=pin)
    assert whole.status_code == 200, whole.text
    assert (whole.json()["state"], whole.json()["refunded_cents"]) == ("refunded", 4000)
    refunds = whole.json()["refunds"]
    assert [(r["provider"], r["amount_cents"], r["external_ref"]) for r in refunds] == [
        ("cash", 500, None), ("cash", 1500, None), ("external", 2000, "REF 99"),
    ]

    report = c.get(f"/reports/{venue.location_id}/closeout", headers=auth(ADMIN_TOKEN)).json()
    assert report["refunds_by_provider"] == {"cash": 2000, "external": 2000}
    assert report["orders"]["refunded"] == 1
    # 4000 given back: the 3700 of sales and the 300 tip.
    assert (report["refund_sales_cents"], report["refund_tips_cents"]) == (3700, 300)
    assert (report["net_sales_cents"], report["net_tips_cents"], report["net_gst_cents"]) == (0, 0, 0)
    receipt = c.get(f"/orders/{order['id']}/receipt", headers=auth(venue.token)).json()
    assert [r["amount_cents"] for r in receipt["refunds"]] == [-500, -1500, -2000]


def _event(venue: Venue, seq: int, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": new_ulid(), "device_id": venue.device_id, "seq": seq,
        "location_id": venue.location_id, "business_date": "2026-11-19",
        "occurred_at": f"2026-11-19T12:00:{seq:02d}+10:00", "type": type_, "payload": payload,
    }


def test_synced_changes_follow_the_same_rules(ctx: Ctx) -> None:
    c, venue, _, s = ctx
    order = _place(c, venue)
    _manager(s, venue)
    disc = {"order_id": order["id"], "adjustment": {
        "id": new_ulid(), "kind": "discount", "basis": "amount", "value": 500}}
    off = {"order_id": order["id"], "line_ids": [new_ulid()], "reason": "x"}
    surcharge = {"order_id": order["id"], "adjustment": {
        "id": new_ulid(), "kind": "surcharge", "basis": "amount", "value": 200}}
    acks = c.post("/sync/push", json={"events": [
        _event(venue, 2, "order.adjusted", disc),
        _event(venue, 3, "order.lines_removed", off),
        _event(venue, 4, "order.adjusted", surcharge),
    ]}, headers=auth(venue.token)).json()["acks"]
    assert [(a["status"], a["code"]) for a in acks] == [
        ("rejected", "manager_approval_required"),
        ("rejected", "unknown_line"),
        ("applied", None),
    ]
    assert c.get(f"/orders/{order['id']}", headers=auth(venue.token)).json()["total_cents"] == 3900
