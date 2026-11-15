from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from void_pos.api.deps import get_printer, get_session
from void_pos.ids import new_ulid
from void_pos.main import app
from void_pos.models import Base, Device, Location, MenuItem, Org

# மசாலா தோசை
TAMIL_DOSA = "மசாலா தୋசை"


class CapturePrinter:
    def __init__(self) -> None:
        self.sent = bytearray()

    def send(self, data: bytes) -> None:
        self.sent += data


@pytest.fixture
def client() -> Iterator[tuple[TestClient, dict[str, str], CapturePrinter]]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions() as s:
        org = Org(name="Madras Curry House")
        s.add(org)
        s.flush()
        loc = Location(org_id=org.id, name="Brisbane CBD")
        s.add(loc)
        s.flush()
        device = Device(
            location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
        )
        item = MenuItem(
            location_id=loc.id, name="Masala Dosa", kitchen_name=TAMIL_DOSA, base_price_cents=1850
        )
        s.add_all([device, item])
        s.flush()
        s.commit()
        ids = {"loc": loc.id, "device": device.id, "item": item.id}

    printer = CapturePrinter()

    def _session() -> Iterator[Session]:
        with sessions() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_printer] = lambda: printer
    try:
        yield TestClient(app), ids, printer
    finally:
        app.dependency_overrides.clear()


def test_place_then_send_to_kitchen(client: tuple[TestClient, dict[str, str], CapturePrinter]) -> None:
    c, ids, printer = client
    r = c.post(
        "/orders",
        json={
            "device_id": ids["device"],
            "location_id": ids["loc"],
            "order_number": 1000,
            "channel": "web",
            "fulfillment": "pickup",
            "lines": [
                {
                    "ref": "a",
                    "menu_item_id": ids["item"],
                    "quantity": 2,
                    "name_snapshot": "Masala Dosa",
                    "kitchen_name_snapshot": TAMIL_DOSA,
                    "unit_price_cents": 1850,
                },
                {
                    "ref": "b",
                    "parent_ref": "a",
                    "quantity": 1,
                    "name_snapshot": "Extra hot",
                    "unit_price_cents": 0,
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "open"
    order_id = body["id"]

    r2 = c.post(f"/orders/{order_id}/send-to-kitchen")
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"] == "sent"
    assert r2.json()["ticket_bytes"] > 0
    assert b"ORDER #1000" in printer.sent


def test_place_order_unknown_device_is_404(
    client: tuple[TestClient, dict[str, str], CapturePrinter],
) -> None:
    c, ids, _ = client
    r = c.post(
        "/orders",
        json={
            "device_id": "00000000000000000000000000",
            "location_id": ids["loc"],
            "order_number": 1,
            "channel": "web",
            "fulfillment": "pickup",
            "lines": [
                {
                    "ref": "a",
                    "menu_item_id": ids["item"],
                    "quantity": 1,
                    "name_snapshot": "X",
                    "unit_price_cents": 100,
                }
            ],
        },
    )
    assert r.status_code == 404


def test_health(client: tuple[TestClient, dict[str, str], CapturePrinter]) -> None:
    c, _, _ = client
    assert c.get("/health").json() == {"status": "ok"}


def test_pay_order_via_api(client: tuple[TestClient, dict[str, str], CapturePrinter]) -> None:
    c, ids, _ = client
    placed = c.post(
        "/orders",
        json={
            "device_id": ids["device"],
            "location_id": ids["loc"],
            "order_number": 1000,
            "channel": "web",
            "fulfillment": "pickup",
            "lines": [
                {
                    "ref": "a",
                    "menu_item_id": ids["item"],
                    "quantity": 2,
                    "name_snapshot": "Masala Dosa",
                    "unit_price_cents": 1850,
                }
            ],
        },
    )
    order_id = placed.json()["id"]

    paid = c.post(f"/orders/{order_id}/pay", json={"method": "cash"})
    assert paid.status_code == 200, paid.text
    body = paid.json()
    assert body["provider"] == "cash"
    assert body["amount_cents"] == 3700
    assert body["order_state"] == "paid"


def test_sync_push_and_pull_via_api(
    client: tuple[TestClient, dict[str, str], CapturePrinter],
) -> None:
    c, ids, _ = client
    event = {
        "event_id": new_ulid(),
        "device_id": ids["device"],
        "seq": 1,
        "location_id": ids["loc"],
        "business_date": "2026-11-15",
        "occurred_at": "2026-11-15T10:00:00+00:00",
        "type": "order.placed",
        "payload": {
            "order_id": new_ulid(),
            "order_number": 1000,
            "channel": "pos",
            "fulfillment": "pickup",
            "lines": [
                {
                    "id": new_ulid(),
                    "menu_item_id": ids["item"],
                    "quantity": 1,
                    "name_snapshot": "Masala Dosa",
                    "unit_price_cents": 1850,
                }
            ],
        },
    }
    push = c.post("/sync/push", json={"events": [event]})
    assert push.status_code == 200, push.text
    assert push.json()["acks"][0]["status"] == "applied"

    pull = c.get("/sync/pull", params={"limit": 50})
    assert pull.status_code == 200, pull.text
    assert event["event_id"] in [e["event_id"] for e in pull.json()["events"]]


def test_export_via_api(client: tuple[TestClient, dict[str, str], CapturePrinter]) -> None:
    c, ids, _ = client
    c.post(
        "/orders",
        json={
            "device_id": ids["device"],
            "location_id": ids["loc"],
            "order_number": 1000,
            "channel": "web",
            "fulfillment": "pickup",
            "lines": [
                {
                    "ref": "a",
                    "menu_item_id": ids["item"],
                    "quantity": 1,
                    "name_snapshot": "Masala Dosa",
                    "unit_price_cents": 1850,
                }
            ],
        },
    )

    bundle = c.get(f"/export/{ids['loc']}")
    assert bundle.status_code == 200, bundle.text
    assert bundle.json()["location"]["id"] == ids["loc"]
    assert len(bundle.json()["orders"]) == 1

    csv_resp = c.get(f"/export/{ids['loc']}/csv/orders")
    assert csv_resp.status_code == 200
    assert csv_resp.headers["content-type"].startswith("text/csv")
    assert "order_number" in csv_resp.text


def test_closeout_via_api(client: tuple[TestClient, dict[str, str], CapturePrinter]) -> None:
    c, ids, _ = client
    placed = c.post(
        "/orders",
        json={
            "device_id": ids["device"],
            "location_id": ids["loc"],
            "order_number": 1000,
            "channel": "web",
            "fulfillment": "pickup",
            "business_date": "2026-11-15",
            "lines": [
                {
                    "ref": "a",
                    "menu_item_id": ids["item"],
                    "quantity": 2,
                    "name_snapshot": "Masala Dosa",
                    "unit_price_cents": 1850,
                }
            ],
        },
    )
    order_id = placed.json()["id"]
    c.post(f"/orders/{order_id}/pay", json={"method": "cash"})

    report = c.get(f"/reports/{ids['loc']}/closeout", params={"business_date": "2026-11-15"})
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["gross_sales_cents"] == 3700
    assert body["payments_by_provider"]["cash"] == 3700
    assert body["orders"]["paid"] == 1
