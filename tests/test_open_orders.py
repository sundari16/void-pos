from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, Venue, api, auth, cmd, make_engine, order_body, seed_venue
from void_pos.ids import new_ulid
from void_pos.totals import gst_cents

Ctx = tuple[TestClient, Venue, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


def _line(qty: int, price: int, rate: str, parent: str | None = None) -> Any:
    return SimpleNamespace(
        id=new_ulid(),
        parent_line_id=parent,
        quantity=qty,
        unit_price_cents=price,
        tax_rate_snapshot=Decimal(rate),
    )


def test_gst_is_the_tax_inside_gst_inclusive_prices() -> None:
    dosa = _line(2, 1850, "0.1")
    lines = [dosa, _line(1, 150, "0.1", parent=dosa.id), _line(1, 600, "0")]
    # (3700 + 300) at 10% carries 4000 / 11 = 363.6 -> 364; the GST-free line adds none.
    assert gst_cents(lines) == 364
    assert gst_cents([_line(1, 1100, "0.1")]) == 100
    assert gst_cents([_line(1, 5, "0.1")]) == 0  # 0.45 rounds down
    assert gst_cents([_line(1, 6, "0.1")]) == 1  # 0.55 rounds up
    assert gst_cents([]) == 0


def _place(c: TestClient, venue: Venue, seq: int, **extra: Any) -> dict[str, Any]:
    body = order_body(venue, seq=seq, order_number=999 + seq, **extra)
    for line in body["lines"]:
        line["tax_rate_snapshot"] = 0.1
    r = c.post("/orders", json=body, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    return dict(r.json())


def test_an_order_comes_back_whole(ctx: Ctx) -> None:
    c, venue, _ = ctx
    placed = _place(c, venue, 1, table_label="12")
    assert placed["table_label"] == "12"
    assert (placed["total_cents"], placed["gst_cents"], placed["balance_cents"]) == (3700, 336, 3700)
    assert [(line["name"], line["quantity"], line["sent"]) for line in placed["lines"]] == [
        ("Masala Dosa", 2, False),
        ("Extra hot", 1, False),
    ]
    assert placed["lines"][1]["parent_line_id"] == placed["lines"][0]["id"]
    assert placed["unsent_lines"] == 2 and placed["payments"] == []

    c.post(
        f"/orders/{placed['id']}/pay",
        json={**cmd(2), "payment_id": new_ulid(), "amount_cents": 1000},
        headers=auth(venue.token),
    )
    again = c.get(f"/orders/{placed['id']}", headers=auth(venue.token)).json()
    assert again["balance_cents"] == 2700
    assert [(p["provider"], p["amount_cents"]) for p in again["payments"]] == [("cash", 1000)]


def test_open_orders_list_the_unsettled_newest_first(ctx: Ctx) -> None:
    c, venue, s = ctx
    first = _place(c, venue, 1, table_label="4")
    second = _place(c, venue, 2, fulfillment="pickup")
    paid = _place(c, venue, 3)
    c.post(
        f"/orders/{paid['id']}/pay",
        json={**cmd(4), "payment_id": new_ulid()},
        headers=auth(venue.token),
    )
    url = f"/locations/{venue.location_id}/orders"

    listed = c.get(url, headers=auth(venue.token)).json()
    assert [(o["order_number"], o["table_label"], o["state"]) for o in listed] == [
        (second["order_number"], None, "open"),
        (first["order_number"], "4", "open"),
    ]
    assert listed[1]["balance_cents"] == 3700 and "lines" not in listed[1]

    only_paid = c.get(url, params={"state": "paid"}, headers=auth(venue.token)).json()
    assert [o["id"] for o in only_paid] == [paid["id"]]
    every = c.get(url, params=[("state", "open"), ("state", "paid")], headers=auth(venue.token))
    assert len(every.json()) == 3
    other_day = c.get(url, params={"business_date": "2001-01-01"}, headers=auth(venue.token))
    assert other_day.json() == []
    assert c.get(url, params={"state": "lost"}, headers=auth(venue.token)).status_code == 422

    admin = c.get(url, headers=auth(ADMIN_TOKEN))
    assert len(admin.json()) == 2
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till-token-open")
    assert c.get(url, headers=auth(other.token)).status_code == 403


def test_a_table_label_is_short_text(ctx: Ctx) -> None:
    c, venue, _ = ctx
    body = order_body(venue, seq=1, table_label="x" * 41)
    assert c.post("/orders", json=body, headers=auth(venue.token)).status_code == 422
