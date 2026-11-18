from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from support import make_engine
from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.menu import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    delete_item,
    get_item,
    list_items,
    update_item,
)
from void_pos.models import (
    Device,
    Location,
    MenuItem,
    MenuItemModifierGroup,
    OrderLine,
    Org,
)


def _session() -> Session:
    engine = make_engine()
    return Session(engine)


def _location(s: Session) -> str:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    return loc.id


def test_item_crud() -> None:
    with _session() as s:
        loc = _location(s)
        item = create_item(
            s, location_id=loc, name="Masala Dosa", base_price_cents=1850, category="Mains"
        )
        s.commit()
        assert [i.id for i in list_items(s, loc)] == [item.id]

        update_item(s, item.id, {"base_price_cents": 1950, "is_available": False})
        s.commit()
        got = get_item(s, item.id)
        assert got.base_price_cents == 1950
        assert got.is_available is False


def test_true_delete_preserves_order_history() -> None:
    with _session() as s:
        loc = _location(s)
        device = Device(
            location_id=loc, name="Till 1", order_number_start=1000, order_number_end=1999
        )
        s.add(device)
        s.flush()
        item = create_item(s, location_id=loc, name="Masala Dosa", base_price_cents=1850)
        s.flush()

        order_id = new_ulid()
        ingest_event(
            s,
            {
                "event_id": new_ulid(),
                "device_id": device.id,
                "seq": 1,
                "location_id": loc,
                "business_date": date(2026, 11, 15),
                "occurred_at": datetime(2026, 11, 15, 10, 0, tzinfo=UTC),
                "type": "order.placed",
                "payload": {
                    "order_id": order_id,
                    "order_number": 1000,
                    "channel": "pos",
                    "fulfillment": "pickup",
                    "lines": [
                        {
                            "id": new_ulid(),
                            "menu_item_id": item.id,
                            "quantity": 1,
                            "name_snapshot": "Masala Dosa",
                            "unit_price_cents": 1850,
                        }
                    ],
                },
            },
        )
        s.commit()

        delete_item(s, item.id)
        s.commit()

        assert s.get(MenuItem, item.id) is None  # truly gone
        assert list_items(s, loc) == []
        # history survives via the snapshot; the FK is nulled
        line = s.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).one()
        assert line.menu_item_id is None
        assert line.name_snapshot == "Masala Dosa"


def test_modifier_group_and_attach() -> None:
    with _session() as s:
        loc = _location(s)
        item = create_item(s, location_id=loc, name="Dosa", base_price_cents=1850)
        group = create_modifier_group(s, location_id=loc, name="Spice", min_select=1, max_select=1)
        modifier = create_modifier(s, group_id=group.id, name="Extra hot", price_delta_cents=0)
        attach_modifier_group(s, menu_item_id=item.id, modifier_group_id=group.id)
        s.commit()

        assert s.get(MenuItemModifierGroup, (item.id, group.id)) is not None
        assert modifier.group_id == group.id


def test_get_missing_item_raises() -> None:
    with _session() as s, pytest.raises(LookupError):
        get_item(s, "00000000000000000000000000")


def test_create_modifier_unknown_group_raises() -> None:
    with _session() as s, pytest.raises(LookupError):
        create_modifier(s, group_id="00000000000000000000000000", name="x")
