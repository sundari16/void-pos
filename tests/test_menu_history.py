"""Menu change history: every edit is recorded, and the latest one can be undone."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, Venue, api, auth, make_engine, seed_venue
from void_pos.errors import Conflict
from void_pos.menu import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    delete_item,
    list_changes,
    revert_change,
    update_item,
)
from void_pos.models import MenuChange, MenuItem, MenuItemModifierGroup


@pytest.fixture
def s() -> Iterator[Session]:
    with Session(make_engine()) as session:
        yield session


def _venue(s: Session) -> Venue:
    return seed_venue(s)


def _changes(s: Session, location_id: str) -> list[tuple[int, str, str]]:
    return [(c.number, c.entity, c.action) for c in reversed(list_changes(s, location_id))]


def test_every_edit_is_recorded_in_order(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    update_item(s, item.id, {"base_price_cents": 450, "name": "Chai"})  # name unchanged
    group = create_modifier_group(s, location_id=v.location_id, name="Milk")
    create_modifier(s, group_id=group.id, name="Oat", price_delta_cents=80)
    attach_modifier_group(s, menu_item_id=item.id, modifier_group_id=group.id)
    delete_item(s, item.id)

    assert _changes(s, v.location_id) == [
        (1, "menu_item", "create"),
        (2, "menu_item", "update"),
        (3, "modifier_group", "create"),
        (4, "modifier", "create"),
        (5, "menu_item", "attach"),
        (6, "menu_item", "delete"),
    ]
    price = list_changes(s, v.location_id)[-2]
    assert (price.before, price.after) == ({"base_price_cents": 400}, {"base_price_cents": 450})


def test_an_edit_that_changes_nothing_is_not_recorded(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    update_item(s, item.id, {"base_price_cents": 400})
    assert len(list_changes(s, v.location_id)) == 1


def test_reverting_a_price_change_restores_it_and_is_itself_revertible(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    update_item(s, item.id, {"base_price_cents": 450})
    change = list_changes(s, v.location_id)[0]

    undo = revert_change(s, change.id)
    assert item.base_price_cents == 400
    assert (undo.action, undo.reverts_id) == ("update", change.id)

    redo = revert_change(s, undo.id)
    assert item.base_price_cents == 450
    assert redo.reverts_id == undo.id


def test_only_the_latest_change_to_something_can_be_reverted(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    update_item(s, item.id, {"base_price_cents": 450})
    update_item(s, item.id, {"name": "Masala Chai"})
    older = list_changes(s, v.location_id)[1]

    with pytest.raises(Conflict) as exc:
        revert_change(s, older.id)
    assert (exc.value.code, exc.value.extra["later_change"]) == ("changed_since", 3)
    assert item.base_price_cents == 450


def test_a_change_is_reverted_once(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    update_item(s, item.id, {"base_price_cents": 450})
    change = list_changes(s, v.location_id)[0]
    revert_change(s, change.id)
    with pytest.raises(Conflict) as exc:
        revert_change(s, change.id)
    assert exc.value.code == "already_reverted"


def test_reverting_a_delete_brings_the_item_back_with_its_groups(s: Session) -> None:
    v = _venue(s)
    group = create_modifier_group(s, location_id=v.location_id, name="Spice")
    attach_modifier_group(s, menu_item_id=v.item_id, modifier_group_id=group.id)
    delete_item(s, v.item_id)
    assert s.get(MenuItem, v.item_id) is None

    revert_change(s, list_changes(s, v.location_id)[0].id)
    restored = s.get(MenuItem, v.item_id)
    assert restored is not None and restored.name == "Masala Dosa"
    links = s.scalars(
        select(MenuItemModifierGroup).where(MenuItemModifierGroup.menu_item_id == v.item_id)
    ).all()
    assert [link.modifier_group_id for link in links] == [group.id]


def test_reverting_a_create_removes_it(s: Session) -> None:
    v = _venue(s)
    item = create_item(s, location_id=v.location_id, name="Chai", base_price_cents=400)
    revert_change(s, list_changes(s, v.location_id)[0].id)
    assert s.get(MenuItem, item.id) is None


def test_a_group_in_use_is_not_removed_by_a_revert(s: Session) -> None:
    v = _venue(s)
    group = create_modifier_group(s, location_id=v.location_id, name="Milk")
    group_change = list_changes(s, v.location_id)[0]
    create_modifier(s, group_id=group.id, name="Oat")
    with pytest.raises(Conflict) as exc:
        revert_change(s, group_change.id)
    assert exc.value.code == "in_use"

    revert_change(s, list_changes(s, v.location_id)[0].id)  # remove the modifier first
    revert_change(s, group_change.id)
    assert s.scalars(select(MenuChange).where(MenuChange.reverts_id == group_change.id)).first()


@pytest.fixture
def client() -> Iterator[tuple[TestClient, Venue]]:
    engine = make_engine()
    with Session(engine) as session:
        venue = seed_venue(session)
    with api(engine) as c:
        yield c, venue


def test_history_and_revert_over_http(client: tuple[TestClient, Venue]) -> None:
    c, v = client
    admin = auth(ADMIN_TOKEN)
    c.patch(f"/menu/items/{v.item_id}", json={"base_price_cents": 1950}, headers=admin)

    history = c.get(f"/locations/{v.location_id}/menu/changes", headers=admin)
    assert history.status_code == 200, history.text
    latest = history.json()[0]
    assert (latest["action"], latest["after"]) == ("update", {"base_price_cents": 1950})
    assert latest["changed_at"].endswith(("Z", "+00:00"))  # timezone-aware UTC

    undone = c.post(f"/menu/changes/{latest['id']}/revert", headers=admin)
    assert undone.status_code == 200, undone.text
    assert undone.json()["reverts_id"] == latest["id"]
    assert c.get(f"/menu/items/{v.item_id}", headers=admin).json()["base_price_cents"] == 1850

    again = c.post(f"/menu/changes/{latest['id']}/revert", headers=admin)
    assert (again.status_code, again.json()["code"]) == (409, "already_reverted")
    missing = c.post("/menu/changes/00000000000000000000000000/revert", headers=admin)
    assert missing.status_code == 404


def test_tills_cannot_read_or_revert_history(client: tuple[TestClient, Venue]) -> None:
    c, v = client
    r = c.get(f"/locations/{v.location_id}/menu/changes", headers=auth(v.token))
    assert (r.status_code, r.json()["code"]) == (403, "admin_only")


def test_history_is_part_of_the_export(client: tuple[TestClient, Venue]) -> None:
    c, v = client
    admin = auth(ADMIN_TOKEN)
    c.patch(f"/menu/items/{v.item_id}", json={"name": "Dosa"}, headers=admin)
    bundle = c.get(f"/export/{v.location_id}", headers=admin).json()
    assert [(m["number"], m["action"]) for m in bundle["menu_changes"]] == [(1, "update")]
