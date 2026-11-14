from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import (
    Base,
    Device,
    Location,
    MenuItem,
    Modifier,
    ModifierGroup,
    Order,
    OrderLine,
    Org,
)
from void_pos.printing import Raster, build_kitchen_ticket, print_order
from void_pos.printing.escpos import CUT, GS, INIT

# மசாலா தோசை
TAMIL_DOSA = "மசாலா தୋசை"


class CapturePrinter:
    def __init__(self) -> None:
        self.sent = bytearray()

    def send(self, data: bytes) -> None:
        self.sent += data


class FakeRasterizer:
    """Deterministic 8x8 raster so the image path is testable without fonts or Pillow."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def render(self, text: str) -> Raster:
        self.calls.append(text)
        return Raster(width=8, height=8, data=bytes([0xFF] * 8))


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _place_order(s: Session, *, kitchen_name: str | None) -> str:
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
        location_id=loc.id, name="Masala Dosa", kitchen_name=kitchen_name, base_price_cents=1850
    )
    group = ModifierGroup(location_id=loc.id, name="Spice level", min_select=0)
    s.add_all([device, item, group])
    s.flush()
    modifier = Modifier(group_id=group.id, name="Extra hot", price_delta_cents=0)
    s.add(modifier)
    s.flush()

    order_id = new_ulid()
    item_line_id = new_ulid()
    event: dict[str, Any] = {
        "event_id": new_ulid(),
        "device_id": device.id,
        "seq": 1,
        "location_id": loc.id,
        "business_date": date(2026, 11, 14),
        "occurred_at": datetime(2026, 11, 14, 10, 0, tzinfo=UTC),
        "type": "order.placed",
        "payload": {
            "order_id": order_id,
            "order_number": 1000,
            "channel": "pos",
            "fulfillment": "pickup",
            "lines": [
                {
                    "id": item_line_id,
                    "menu_item_id": item.id,
                    "quantity": 2,
                    "name_snapshot": item.name,
                    "kitchen_name_snapshot": item.kitchen_name,
                    "unit_price_cents": 1850,
                },
                {
                    "id": new_ulid(),
                    "parent_line_id": item_line_id,
                    "modifier_id": modifier.id,
                    "quantity": 1,
                    "name_snapshot": modifier.name,
                    "unit_price_cents": 0,
                },
            ],
        },
    }
    ingest_event(s, event)
    s.commit()
    return order_id


def test_non_latin_item_is_rasterized() -> None:
    with _session() as s:
        order_id = _place_order(s, kitchen_name=TAMIL_DOSA)
        printer = CapturePrinter()
        rasterizer = FakeRasterizer()

        data = print_order(s, order_id, printer, rasterizer=rasterizer)

        assert data.startswith(INIT)
        assert data.endswith(CUT)
        assert b"ORDER #1000" in data
        assert b"pos / pickup" in data
        assert GS + b"v0" in data  # the Tamil item name took the raster path
        assert any(TAMIL_DOSA in call for call in rasterizer.calls)
        assert b"  + Extra hot" in data  # the ASCII modifier stayed as text
        assert printer.sent == data


def test_ascii_ticket_needs_no_rasterizer() -> None:
    with _session() as s:
        order_id = _place_order(s, kitchen_name=None)  # falls back to the ASCII name
        printer = CapturePrinter()

        data = print_order(s, order_id, printer)  # no rasterizer supplied

        assert GS + b"v0" not in data  # nothing needed rasterizing
        assert b"2x Masala Dosa" in data
        assert b"  + Extra hot" in data
        assert printer.sent == data


def test_build_kitchen_ticket_nests_modifiers_under_item() -> None:
    with _session() as s:
        order_id = _place_order(s, kitchen_name=None)
        order = s.get(Order, order_id)
        lines = s.scalars(
            select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
        ).all()

        ticket = build_kitchen_ticket(order, lines)

        assert ticket.order_number == 1000
        assert [tl.is_modifier for tl in ticket.lines] == [False, True]
        assert ticket.lines[0].quantity == 2
