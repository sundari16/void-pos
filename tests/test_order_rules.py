from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import Venue, api, auth, make_engine, order_body, seed_venue
from void_pos.clock import local_time, utcnow
from void_pos.ids import new_ulid
from void_pos.menu import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    update_item,
    update_modifier_group,
)
from void_pos.models import Device

Ctx = tuple[TestClient, Venue, Session, dict[str, str]]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    """A venue whose dosa has a required single-choice spice level, and an extras group
    that applies to nothing."""
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        spice = create_modifier_group(
            s, location_id=venue.location_id, name="Spice level", min_select=1, max_select=1
        )
        extras = create_modifier_group(s, location_id=venue.location_id, name="Extras")
        ids = {
            "spice": spice.id,
            "mild": create_modifier(s, group_id=spice.id, name="Mild").id,
            "hot": create_modifier(s, group_id=spice.id, name="Hot").id,
            "butter": create_modifier(s, group_id=extras.id, name="Butter", price_delta_cents=100).id,
        }
        attach_modifier_group(s, menu_item_id=venue.item_id, modifier_group_id=spice.id)
        s.commit()
        with api(engine) as client:
            yield client, venue, s, ids


def _order(venue: Venue, seq: int, *mods: tuple[str | None, int], **extra: Any) -> dict[str, Any]:
    """A dosa order with a modifier line per (modifier id or None for a note, quantity)."""
    body = order_body(venue, seq=seq, order_number=999 + seq, with_modifier=False, **extra)
    item_line = body["lines"][0]["id"]
    for modifier_id, quantity in mods:
        body["lines"].append(
            {
                "id": new_ulid(),
                "parent_line_id": item_line,
                "modifier_id": modifier_id,
                "quantity": quantity,
                "name_snapshot": "choice",
                "unit_price_cents": 0,
            }
        )
    return body


def _post(c: TestClient, venue: Venue, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    r = c.post("/orders", json=body, headers=auth(venue.token))
    return r.status_code, r.json()


def test_one_required_choice_places(ctx: Ctx) -> None:
    c, venue, _, ids = ctx
    status, body = _post(c, venue, _order(venue, 1, (ids["mild"], 1), (None, 1)))
    assert status == 200, body  # a free-text note rides along


@pytest.mark.parametrize(
    "picks",
    [(), (("mild", 1), ("hot", 1)), (("hot", 2),)],
    ids=["none", "two", "double"],
)
def test_choices_outside_the_groups_limits_are_refused(
    ctx: Ctx, picks: tuple[tuple[str, int], ...]
) -> None:
    c, venue, s, ids = ctx
    status, body = _post(c, venue, _order(venue, 1, *((ids[k], q) for k, q in picks)))
    assert (status, body["code"]) == (422, "modifier_selection")
    assert body["modifier_group_id"] == ids["spice"]
    assert (body["min_select"], body["max_select"]) == (1, 1)
    # Nothing was recorded: the till can reuse the same seq once it fixes the order.
    device = s.get(Device, venue.device_id)
    s.refresh(device)
    assert device is not None and device.last_seq == 0
    status, _ = _post(c, venue, _order(venue, 1, (ids["mild"], 1)))
    assert status == 200


def test_a_modifier_from_a_group_the_item_does_not_have_is_refused(ctx: Ctx) -> None:
    c, venue, _, ids = ctx
    status, body = _post(c, venue, _order(venue, 1, (ids["mild"], 1), (ids["butter"], 1)))
    assert (status, body["code"], body["modifier_id"]) == (422, "modifier_not_allowed", ids["butter"])


def test_unknown_modifiers_and_choices_on_open_items_are_refused(ctx: Ctx) -> None:
    c, venue, _, ids = ctx
    status, body = _post(c, venue, _order(venue, 1, (ids["mild"], 1), (new_ulid(), 1)))
    assert (status, body["code"]) == (422, "modifier_not_allowed")

    open_item = _order(venue, 1, (ids["mild"], 1))
    open_item["lines"][0]["menu_item_id"] = None
    status, body = _post(c, venue, open_item)
    assert (status, body["code"]) == (422, "modifier_not_allowed")

    open_item["lines"].pop()  # an open item with no catalog choices is fine
    assert _post(c, venue, open_item)[0] == 200


def test_an_item_switched_off_or_removed_is_refused(ctx: Ctx) -> None:
    c, venue, s, ids = ctx
    update_item(s, venue.item_id, {"is_available": False})
    s.commit()
    status, body = _post(c, venue, _order(venue, 1, (ids["mild"], 1)))
    assert (status, body["code"]) == (409, "item_unavailable")
    assert body["menu_item_id"] == venue.item_id


def _window_excluding_now(timezone: str) -> dict[str, Any]:
    """A daily window that starts two hours from now and lasts an hour."""
    later = local_time(timezone, utcnow() + timedelta(hours=2))
    end = local_time(timezone, utcnow() + timedelta(hours=3))
    return {"available_from": later, "available_to": end}


def test_an_item_outside_its_window_is_refused(ctx: Ctx) -> None:
    c, venue, s, ids = ctx
    item = create_item(
        s,
        location_id=venue.location_id,
        name="Thali",
        base_price_cents=2200,
        **_window_excluding_now("Australia/Brisbane"),
    )
    s.commit()
    body = _order(venue, 1, (ids["mild"], 1))
    body["lines"].append(
        {"id": new_ulid(), "menu_item_id": item.id, "name_snapshot": "Thali", "unit_price_cents": 2200}
    )
    status, refused = _post(c, venue, body)
    assert (status, refused["code"], refused["menu_item_id"]) == (409, "item_unavailable", item.id)
    assert "isn't on sale at" in refused["detail"]


def test_a_group_outside_its_window_has_no_minimum_and_no_choices(ctx: Ctx) -> None:
    c, venue, s, ids = ctx
    update_modifier_group(s, ids["spice"], _window_excluding_now("Australia/Brisbane"))
    s.commit()
    status, body = _post(c, venue, _order(venue, 1, (ids["mild"], 1)))
    assert (status, body["code"]) == (409, "modifier_unavailable")
    status, body = _post(c, venue, _order(venue, 1))
    assert status == 200, body


def test_offline_orders_synced_later_are_recorded_as_rung_up(ctx: Ctx) -> None:
    c, venue, s, _ = ctx
    update_item(s, venue.item_id, {"is_available": False})
    s.commit()
    body = _order(venue, 1)  # unavailable item, and no spice level chosen
    event = {
        "event_id": body.pop("event_id"),
        "seq": body.pop("seq"),
        "device_id": venue.device_id,
        "location_id": venue.location_id,
        "business_date": "2026-11-18",
        "occurred_at": utcnow().isoformat(),
        "type": "order.placed",
        "payload": body,
    }
    r = c.post("/sync/push", json={"events": [event]}, headers=auth(venue.token))
    assert r.status_code == 200, r.text
    assert r.json()["acks"][0]["status"] == "applied"
