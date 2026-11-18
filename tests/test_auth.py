"""Pairing, tokens and who may do what."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, Venue, api, auth, make_engine, order_body, seed_venue
from void_pos.clock import utcnow
from void_pos.config import Settings
from void_pos.models import Device


@pytest.fixture
def ctx() -> Iterator[tuple[TestClient, Venue, Session]]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


Ctx = tuple[TestClient, Venue, Session]
ADMIN = auth(ADMIN_TOKEN)


def _register(c: TestClient, location_id: str, *, name: str = "Till 2", start: int = 2000) -> dict:
    r = c.post(
        f"/admin/locations/{location_id}/devices",
        json={"name": name, "order_number_start": start, "order_number_end": start + 999},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_full_venue_setup_and_pairing() -> None:
    engine = make_engine()
    with api(engine) as c:
        loc = c.post(
            "/admin/locations",
            json={"org_name": "Madras Curry House", "name": "Brisbane CBD"},
            headers=ADMIN,
        )
        assert loc.status_code == 201, loc.text
        location_id = loc.json()["id"]
        assert loc.json()["timezone"] == "Australia/Brisbane"

        created = _register(c, location_id, name="Till 1", start=1000)
        assert created["device"]["paired"] is False
        code = created["pairing_code"]
        assert len(code) == 9 and code[4] == "-"

        paired = c.post("/devices/pair", json={"pairing_code": code.lower().replace("-", " ")})
        assert paired.status_code == 200, paired.text
        body = paired.json()
        assert body["device"]["paired"] is True
        assert body["location"]["id"] == location_id
        assert body["device"]["order_number_start"] == 1000

        me = c.get("/devices/me", headers=auth(body["token"]))
        assert me.status_code == 200, me.text
        assert me.json()["device"]["id"] == created["device"]["id"]

        reused = c.post("/devices/pair", json={"pairing_code": code})
        assert (reused.status_code, reused.json()["code"]) == (401, "invalid_pairing_code")

        listed = c.get(f"/admin/locations/{location_id}/devices", headers=ADMIN)
        assert [d["paired"] for d in listed.json()] == [True]


def test_requests_without_a_valid_token_are_unauthorized(ctx: Ctx) -> None:
    c, venue, _ = ctx
    missing = c.get(f"/locations/{venue.location_id}/menu/items")
    assert (missing.status_code, missing.json()["code"]) == (401, "missing_token")
    wrong = c.get(f"/locations/{venue.location_id}/menu/items", headers=auth("nope"))
    assert (wrong.status_code, wrong.json()["code"]) == (401, "invalid_token")


def test_an_expired_pairing_code_is_refused(ctx: Ctx) -> None:
    c, venue, s = ctx
    code = _register(c, venue.location_id)
    device = s.get(Device, code["device"]["id"])
    assert device is not None
    s.refresh(device)
    device.pairing_expires_at = utcnow() - timedelta(seconds=1)
    s.commit()
    r = c.post("/devices/pair", json={"pairing_code": code["pairing_code"]})
    assert (r.status_code, r.json()["code"]) == (401, "pairing_code_expired")


def test_re_pairing_replaces_the_old_token(ctx: Ctx) -> None:
    c, venue, _ = ctx
    created = _register(c, venue.location_id)
    first = c.post("/devices/pair", json={"pairing_code": created["pairing_code"]}).json()["token"]
    fresh = c.post(f"/admin/devices/{created['device']['id']}/pairing-code", headers=ADMIN)
    assert fresh.status_code == 200, fresh.text
    second = c.post("/devices/pair", json={"pairing_code": fresh.json()["pairing_code"]}).json()["token"]

    assert c.get("/devices/me", headers=auth(first)).status_code == 401
    assert c.get("/devices/me", headers=auth(second)).status_code == 200


def test_revoking_a_till_cuts_it_off(ctx: Ctx) -> None:
    c, venue, _ = ctx
    revoked = c.post(f"/admin/devices/{venue.device_id}/revoke", headers=ADMIN)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None
    assert c.get("/devices/me", headers=auth(venue.token)).status_code == 401
    again = c.post(f"/admin/devices/{venue.device_id}/pairing-code", headers=ADMIN)
    assert (again.status_code, again.json()["code"]) == (409, "device_revoked")


def test_the_admin_token_cannot_ring_up_orders(ctx: Ctx) -> None:
    c, venue, _ = ctx
    r = c.post("/orders", json=order_body(venue, seq=1), headers=ADMIN)
    assert (r.status_code, r.json()["code"]) == (403, "till_only")


def test_a_till_cannot_use_admin_routes(ctx: Ctx) -> None:
    c, venue, _ = ctx
    r = c.get("/admin/locations", headers=auth(venue.token))
    assert (r.status_code, r.json()["code"]) == (403, "admin_only")


def test_admin_routes_are_off_without_an_admin_token() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, admin_token=None) as c:
        r = c.get("/admin/locations", headers=auth(venue.token))
    assert (r.status_code, r.json()["code"]) == (403, "admin_disabled")


def test_a_till_only_sees_its_own_location(ctx: Ctx) -> None:
    c, venue, s = ctx
    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till")
    menu = c.get(f"/locations/{other.location_id}/menu/items", headers=auth(venue.token))
    assert (menu.status_code, menu.json()["code"]) == (403, "wrong_location")
    report = c.get(f"/reports/{other.location_id}/closeout", headers=auth(venue.token))
    assert report.status_code == 403
    item = c.get(f"/menu/items/{other.item_id}", headers=auth(venue.token))
    assert item.status_code == 403


def test_order_number_ranges_cannot_overlap(ctx: Ctx) -> None:
    c, venue, _ = ctx
    r = c.post(
        f"/admin/locations/{venue.location_id}/devices",
        json={"name": "Till 2", "order_number_start": 1500, "order_number_end": 2500},
        headers=ADMIN,
    )
    assert (r.status_code, r.json()["code"]) == (409, "range_overlap")
    assert "Till 1" in r.json()["detail"]


def test_setup_input_is_validated(ctx: Ctx) -> None:
    c, venue, _ = ctx
    tz = c.post(
        "/admin/locations", json={"org_name": "X", "name": "Y", "timezone": "Mars/Base"}, headers=ADMIN
    )
    assert (tz.status_code, tz.json()["code"]) == (422, "invalid_timezone")
    backwards = c.post(
        f"/admin/locations/{venue.location_id}/devices",
        json={"name": "Till 2", "order_number_start": 3000, "order_number_end": 2000},
        headers=ADMIN,
    )
    assert backwards.status_code == 422
    nowhere = c.post(
        "/admin/locations/00000000000000000000000000/devices",
        json={"name": "Till 2", "order_number_start": 3000, "order_number_end": 3999},
        headers=ADMIN,
    )
    assert nowhere.status_code == 404


def test_cors_preflight_allows_the_authorization_header(ctx: Ctx) -> None:
    c, _, _ = ctx
    r = c.options(
        "/orders",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert r.status_code == 200
    assert "authorization" in r.headers["access-control-allow-headers"].lower()


def test_a_short_admin_token_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_token="short")  # type: ignore[call-arg]
    assert Settings(_env_file=None, admin_token="  ").admin_token is None  # type: ignore[call-arg]
