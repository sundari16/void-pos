from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, Venue, api, auth, make_engine, seed_venue
from void_pos.api.ratelimit import AttemptLimiter, TooManyAttempts
from void_pos.devices import issue_pairing_code
from void_pos.menu import create_item, list_changes, list_items
from void_pos.models import Device

ADMIN = auth(ADMIN_TOKEN)
Ctx = tuple[TestClient, Venue, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


# --- bulk menu edit ----------------------------------------------------------------


def _bulk(c: TestClient, venue: Venue, updates: list[dict[str, Any]], headers: Any = ADMIN) -> Any:
    return c.patch(
        f"/locations/{venue.location_id}/menu/items", json={"updates": updates}, headers=headers
    )


def test_a_bulk_edit_applies_every_change_with_its_own_history(ctx: Ctx) -> None:
    c, venue, s = ctx
    naan = create_item(s, location_id=venue.location_id, name="Garlic Naan", base_price_cents=450)
    s.commit()

    r = _bulk(
        c,
        venue,
        [
            {"id": venue.item_id, "base_price_cents": 1950},
            {"id": naan.id, "is_available": False, "available_from": "17:00"},
        ],
    )
    assert r.status_code == 200, r.text
    assert [(i["name"], i["base_price_cents"], i["is_available"]) for i in r.json()] == [
        ("Masala Dosa", 1950, True),
        ("Garlic Naan", 450, False),
    ]
    s.expire_all()
    changes = list_changes(s, venue.location_id)
    assert [(ch.entity_id, ch.after) for ch in changes[:2]] == [
        (naan.id, {"is_available": False, "available_from": "17:00:00"}),
        (venue.item_id, {"base_price_cents": 1950}),
    ]


def test_one_bad_edit_leaves_every_item_as_it_was(ctx: Ctx) -> None:
    c, venue, s = ctx
    naan = create_item(s, location_id=venue.location_id, name="Garlic Naan", base_price_cents=450)
    s.commit()
    before = len(list_changes(s, venue.location_id, limit=500))

    r = _bulk(
        c,
        venue,
        [
            {"id": venue.item_id, "base_price_cents": 1950},
            {"id": naan.id, "available_from": "11:00", "available_to": "11:00"},
        ],
    )
    assert (r.status_code, r.json()["code"], r.json()["item_id"]) == (
        422,
        "invalid_time_window",
        naan.id,
    )
    assert r.json()["detail"].startswith("Garlic Naan: ")
    s.expire_all()
    assert {i.name: i.base_price_cents for i in list_items(s, venue.location_id)} == {
        "Garlic Naan": 450,
        "Masala Dosa": 1850,
    }
    assert len(list_changes(s, venue.location_id, limit=500)) == before


def test_bulk_edits_name_items_on_this_menu_once_each(ctx: Ctx) -> None:
    c, venue, s = ctx
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till-token-abc")
    r = _bulk(c, venue, [{"id": other.item_id, "base_price_cents": 1}])
    assert (r.status_code, r.json()["item_ids"]) == (404, [other.item_id])
    twice = _bulk(c, venue, [{"id": venue.item_id}, {"id": venue.item_id, "name": "Dosa"}])
    assert (twice.status_code, twice.json()["code"]) == (422, "duplicate_item")
    assert _bulk(c, venue, []).status_code == 422
    cleared = _bulk(c, venue, [{"id": venue.item_id, "name": None}])
    assert cleared.status_code == 422
    till = _bulk(c, venue, [{"id": venue.item_id, "base_price_cents": 1}], headers=auth(venue.token))
    assert till.status_code == 403


# --- pairing attempts ---------------------------------------------------------------


def test_the_limiter_counts_failures_per_client_and_overall() -> None:
    now = [1000.0]
    limiter = AttemptLimiter(per_client=3, overall=5, window=60, clock=lambda: now[0])
    for _ in range(3):
        limiter.check("10.0.0.5")
        limiter.failed("10.0.0.5")
    with pytest.raises(TooManyAttempts) as exc:
        limiter.check("10.0.0.5")
    assert exc.value.extra["retry_after"] == 60
    limiter.check("10.0.0.6")  # another till is unaffected...
    limiter.failed("10.0.0.6")
    limiter.failed("10.0.0.7")
    with pytest.raises(TooManyAttempts):
        limiter.check("10.0.0.8")  # ...until everyone together hits the backstop
    now[0] += 61
    limiter.check("10.0.0.5")
    limiter.check("10.0.0.8")


def test_wrong_codes_are_throttled_but_the_right_one_still_works_later(ctx: Ctx) -> None:
    c, venue, s = ctx
    device = Device(
        location_id=venue.location_id, name="Till 2", order_number_start=2000, order_number_end=2999
    )
    s.add(device)
    s.flush()
    code = issue_pairing_code(s, device, ttl=timedelta(minutes=15)).code
    s.commit()

    for _ in range(10):
        wrong = c.post("/devices/pair", json={"pairing_code": "AAAA-AAAA"})
        assert (wrong.status_code, wrong.json()["code"]) == (401, "invalid_pairing_code")
    blocked = c.post("/devices/pair", json={"pairing_code": code})
    assert (blocked.status_code, blocked.json()["code"]) == (429, "too_many_attempts")
    assert int(blocked.headers["Retry-After"]) > 0

    # Behind a reverse proxy that sets X-Forwarded-For, other clients are unaffected.
    other = c.post(
        "/devices/pair", json={"pairing_code": code}, headers={"X-Forwarded-For": "192.168.1.40"}
    )
    assert other.status_code == 200, other.text
