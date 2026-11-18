from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, Venue, api, auth, cmd, make_engine, order_body, seed_venue
from void_pos.cli import main
from void_pos.devices import create_location
from void_pos.errors import Conflict, Forbidden, InvalidRequest
from void_pos.menu import list_changes
from void_pos.models import Base, Event, Staff
from void_pos.staff import approving_manager, create_staff, deactivate_staff, set_pin
from void_pos.staff.service import pin_matches

ADMIN = auth(ADMIN_TOKEN)
Ctx = tuple[TestClient, Venue, Session]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s


def test_pins_are_hashed_unique_and_checked() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        asha = create_staff(s, location_id=loc, name=" Asha ", role="manager", pin="4821")
        assert asha.name == "Asha" and "4821" not in asha.pin_hash
        assert pin_matches("4821", asha.pin_hash) and not pin_matches("4822", asha.pin_hash)

        with pytest.raises(Conflict) as taken:
            create_staff(s, location_id=loc, name="Ravi", role="staff", pin="4821")
        assert taken.value.code == "pin_taken"
        for bad in ("123", "123456789", "12a4"):
            with pytest.raises(InvalidRequest):
                create_staff(s, location_id=loc, name="Ravi", role="staff", pin=bad)
        with pytest.raises(InvalidRequest):
            create_staff(s, location_id=loc, name="Ravi", role="owner", pin="1111")

        assert approving_manager(s, loc, "4821") is asha
        with pytest.raises(Forbidden) as wrong:
            approving_manager(s, loc, "0000")
        assert wrong.value.code == "wrong_pin"
        with pytest.raises(Forbidden) as missing:
            approving_manager(s, loc, None)
        assert missing.value.code == "manager_pin_required"

        ravi = create_staff(s, location_id=loc, name="Ravi", role="staff", pin="7777")
        with pytest.raises(Forbidden):
            approving_manager(s, loc, "7777")  # staff, not a manager
        set_pin(s, asha.id, "5555")
        assert approving_manager(s, loc, "5555") is asha
        deactivate_staff(s, asha.id)
        assert approving_manager(s, loc, None) is None  # no managers left: no approval
        assert ravi.active


def _manager(c: TestClient, venue: Venue, pin: str = "4821") -> dict[str, Any]:
    r = c.post(
        f"/admin/locations/{venue.location_id}/staff",
        json={"name": "Asha", "role": "manager", "pin": pin},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    assert "pin" not in r.json() and "pin_hash" not in r.json()
    return dict(r.json())


def test_staff_admin_endpoints(ctx: Ctx) -> None:
    c, venue, _ = ctx
    asha = _manager(c, venue)
    listed = c.get(f"/admin/locations/{venue.location_id}/staff", headers=ADMIN).json()
    assert [(p["name"], p["role"]) for p in listed] == [("Asha", "manager")]
    assert c.post(f"/admin/staff/{asha['id']}/pin", json={"pin": "12"}, headers=ADMIN).status_code == 422
    assert c.post(f"/admin/staff/{asha['id']}/pin", json={"pin": "9090"}, headers=ADMIN).status_code == 200
    off = c.post(f"/admin/staff/{asha['id']}/deactivate", headers=ADMIN).json()
    assert off["active"] is False
    assert c.get(f"/admin/locations/{venue.location_id}/staff", headers=ADMIN).json() == []
    till = c.get(f"/admin/locations/{venue.location_id}/staff", headers=auth(venue.token))
    assert till.status_code == 403


def test_a_void_needs_a_managers_pin_once_there_is_a_manager(ctx: Ctx) -> None:
    c, venue, s = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    _manager(c, venue)

    missing = c.post(f"/orders/{order['id']}/void", json={**cmd(2), "reason": "Left"}, headers=till)
    assert (missing.status_code, missing.json()["code"]) == (403, "manager_pin_required")
    wrong = c.post(
        f"/orders/{order['id']}/void",
        json={**cmd(2), "reason": "Left", "manager_pin": "0000"},
        headers=till,
    )
    assert (wrong.status_code, wrong.json()["code"]) == (403, "wrong_pin")
    ok = c.post(
        f"/orders/{order['id']}/void",
        json={**cmd(2), "reason": "Left", "manager_pin": "4821"},
        headers=till,
    )
    assert ok.status_code == 200, ok.text
    event = s.scalars(select(Event).where(Event.type == "order.voided")).one()
    asha = s.scalars(select(Staff)).one()
    assert (event.payload["approved_by"], event.payload["approved_by_name"]) == (asha.id, "Asha")


def test_wrong_pins_are_throttled_per_till(ctx: Ctx) -> None:
    c, venue, _ = ctx
    till = auth(venue.token)
    order = c.post("/orders", json=order_body(venue, seq=1), headers=till).json()
    _manager(c, venue)
    body = {**cmd(2), "reason": "Left", "manager_pin": "0000"}
    for _ in range(5):
        assert c.post(f"/orders/{order['id']}/void", json=body, headers=till).status_code == 403
    blocked = c.post(
        f"/orders/{order['id']}/void", json={**body, "manager_pin": "4821"}, headers=till
    )
    assert (blocked.status_code, blocked.json()["code"]) == (429, "too_many_attempts")
    assert "PINs" in blocked.json()["detail"] and "Retry-After" in blocked.headers


def test_a_till_switches_a_dish_off_with_a_managers_pin(ctx: Ctx) -> None:
    c, venue, s = ctx
    till = auth(venue.token)
    url = f"/menu/items/{venue.item_id}/availability"

    # No manager yet: any till can do it, and the history says which till.
    off = c.post(url, json={"is_available": False}, headers=till)
    assert (off.status_code, off.json()["is_available"]) == (200, False)
    _manager(c, venue)
    assert c.post(url, json={"is_available": True}, headers=till).status_code == 403
    on = c.post(url, json={"is_available": True, "manager_pin": "4821"}, headers=till)
    assert on.json()["is_available"] is True
    by_admin = c.post(url, json={"is_available": False}, headers=ADMIN)
    assert by_admin.status_code == 200

    s.expire_all()
    actors = [ch.actor for ch in list_changes(s, venue.location_id)[:3]]
    assert actors == ["admin", "Asha on Till 1", "Till 1"]

    other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till-token-pin")
    foreign = c.post(url, json={"is_available": True}, headers=auth(other.token))
    assert foreign.status_code == 403


def test_staff_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
    engine.dispose()

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"4821\n")))
    args = ["--database-url", url, "add-staff", "--location", loc, "--name", "Asha"]
    assert main([*args, "--role", "manager"]) == 0
    added = capsys.readouterr().out
    assert added.startswith("Added manager Asha: ")
    staff_id = added.strip().rsplit(" ", 1)[-1]

    assert main(["--database-url", url, "list-staff", "--location", loc]) == 0
    assert "manager  Asha" in capsys.readouterr().out
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"12\n")))
    assert main([*args]) == 1
    assert "4 to 8 digits" in capsys.readouterr().err
    assert main(["--database-url", url, "deactivate-staff", "--staff", staff_id]) == 0
    assert "no longer works" in capsys.readouterr().out
