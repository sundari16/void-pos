from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, api, auth, make_engine, seed_venue
from void_pos.cli import main
from void_pos.devices import create_location
from void_pos.errors import InvalidRequest
from void_pos.menu import (
    create_item,
    delete_item,
    list_changes,
    list_modifier_groups,
    revert_change,
    till_menu,
    update_item,
)
from void_pos.menu.availability import in_window
from void_pos.menu.modifier_import import import_modifiers, parse_modifier_csv
from void_pos.models import Base

ADMIN = auth(ADMIN_TOKEN)


# --- availability windows ------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "at", "expected"),
    [
        (None, None, "03:00", True),
        ("11:00", "15:00", "11:00", True),
        ("11:00", "15:00", "15:00", False),  # the end is exclusive
        ("11:00", "15:00", "10:59", False),
        ("22:00", "02:00", "23:30", True),  # past midnight
        ("22:00", "02:00", "01:59", True),
        ("22:00", "02:00", "12:00", False),
        ("17:00", None, "23:59", True),  # from 17:00 until midnight
        ("17:00", None, "16:59", False),
        (None, "11:00", "00:00", True),  # from midnight until 11:00
        (None, "11:00", "11:00", False),
    ],
)
def test_in_window(start: str | None, end: str | None, at: str, expected: bool) -> None:
    def t(text: str | None) -> time | None:
        return time.fromisoformat(text) if text else None

    assert in_window(t(start), t(end), time.fromisoformat(at)) is expected


def test_a_window_that_starts_and_ends_together_is_refused() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD")
        with pytest.raises(InvalidRequest, match="same time"):
            create_item(
                s,
                location_id=loc.id,
                name="Thali",
                base_price_cents=2200,
                available_from=time(11),
                available_to=time(11),
            )


def test_window_edits_are_recorded_and_revert() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD")
        item = create_item(s, location_id=loc.id, name="Thali", base_price_cents=2200)
        update_item(s, item.id, {"available_from": time(11), "available_to": time(15)})
        change = list_changes(s, loc.id)[0]
        assert change.after == {"available_from": "11:00:00", "available_to": "15:00:00"}

        revert_change(s, change.id)
        assert (item.available_from, item.available_to) == (None, None)
        undo = list_changes(s, loc.id)[0]
        revert_change(s, undo.id)
        assert (item.available_from, item.available_to) == (time(11), time(15))

        # A deleted item comes back with its window.
        delete_item(s, item.id)
        assert till_menu(s, loc.id).items == []
        revert_change(s, list_changes(s, loc.id)[0].id)
        s.expire_all()
        restored = till_menu(s, loc.id).items[0].item
        assert (restored.available_from, restored.available_to) == (time(11), time(15))


# --- the till's menu ---------------------------------------------------------------


def _menu(client: Any, venue: Any) -> dict[str, Any]:
    response = client.get(f"/locations/{venue.location_id}/menu", headers=auth(venue.token))
    assert response.status_code == 200, response.text
    return response.json()


def test_the_till_menu_lists_items_with_their_groups_and_modifiers() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine) as client:
        group = client.post(
            f"/locations/{venue.location_id}/menu/modifier-groups",
            json={"name": "Spice level", "min_select": 1, "max_select": 1},
            headers=ADMIN,
        ).json()
        for name in ("Mild", "Medium", "Hot"):
            client.post(
                f"/menu/modifier-groups/{group['id']}/modifiers",
                json={"name": name},
                headers=ADMIN,
            )
        client.post(f"/menu/items/{venue.item_id}/modifier-groups/{group['id']}", headers=ADMIN)
        lunch = client.post(
            f"/locations/{venue.location_id}/menu/items",
            json={"name": "Thali", "base_price_cents": 2200, "available_from": "11:00",
                  "available_to": "15:00"},
            headers=ADMIN,
        ).json()
        assert lunch["available_from"] == "11:00:00"

        menu = _menu(client, venue)
        assert menu["timezone"] == "Australia/Brisbane"
        items = {item["name"]: item for item in menu["items"]}
        assert items["Masala Dosa"]["modifier_group_ids"] == [group["id"]]
        assert items["Masala Dosa"]["available_now"] is True
        assert items["Thali"]["modifier_group_ids"] == []
        [spice] = menu["modifier_groups"]
        assert [m["name"] for m in spice["modifiers"]] == ["Mild", "Medium", "Hot"]
        assert (spice["min_select"], spice["max_select"], spice["available_now"]) == (1, 1, True)


def test_availability_follows_the_location_clock() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        create_item(
            s,
            location_id=venue.location_id,
            name="Late supper",
            base_price_cents=1500,
            available_from=time(22),
            available_to=time(2),
        )
        # 13:30 UTC is 23:30 in Brisbane: on. 03:00 UTC is 13:00 there: off.
        late = datetime(2026, 11, 18, 13, 30, tzinfo=UTC)
        day = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)
        on = {e.item.name: e.available_now for e in till_menu(s, venue.location_id, late).items}
        off = {e.item.name: e.available_now for e in till_menu(s, venue.location_id, day).items}
        assert on == {"Late supper": True, "Masala Dosa": True}
        assert off == {"Late supper": False, "Masala Dosa": True}
        assert till_menu(s, venue.location_id, late).local_time == time(23, 30)


def test_an_item_switched_off_is_listed_but_not_available() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine) as client:
        client.patch(f"/menu/items/{venue.item_id}", json={"is_available": False}, headers=ADMIN)
        [item] = _menu(client, venue)["items"]
        assert (item["is_available"], item["available_now"]) == (False, False)


def test_menu_reads_are_scoped_and_bad_windows_are_refused() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        other = create_location(s, org_name="MCH", name="Fortitude Valley").id
        s.commit()
    with api(engine) as client:
        response = client.get(f"/locations/{other}/menu", headers=auth(venue.token))
        assert (response.status_code, response.json()["code"]) == (403, "wrong_location")

        aware = client.patch(
            f"/menu/items/{venue.item_id}", json={"available_from": "11:00+10:00"}, headers=ADMIN
        )
        assert aware.status_code == 422
        same = client.patch(
            f"/menu/items/{venue.item_id}",
            json={"available_from": "11:00", "available_to": "11:00"},
            headers=ADMIN,
        )
        assert (same.status_code, same.json()["code"]) == (422, "invalid_time_window")


def test_groups_and_modifiers_can_be_edited_and_reverted() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine) as client:
        group = client.post(
            f"/locations/{venue.location_id}/menu/modifier-groups",
            json={"name": "Extras", "max_select": 3},
            headers=ADMIN,
        ).json()
        butter = client.post(
            f"/menu/modifier-groups/{group['id']}/modifiers",
            json={"name": "Butter", "price_delta_cents": 100},
            headers=ADMIN,
        ).json()

        edited = client.patch(
            f"/menu/modifier-groups/{group['id']}",
            json={"max_select": 2, "available_from": "17:00"},
            headers=ADMIN,
        )
        assert edited.status_code == 200, edited.text
        assert (edited.json()["max_select"], edited.json()["available_from"]) == (2, "17:00:00")
        bad = client.patch(
            f"/menu/modifier-groups/{group['id']}", json={"min_select": 3}, headers=ADMIN
        )
        assert (bad.status_code, bad.json()["code"]) == (422, "invalid_selection_limits")
        cleared = client.patch(
            f"/menu/modifier-groups/{group['id']}", json={"name": None}, headers=ADMIN
        )
        assert cleared.status_code == 422

        priced = client.patch(
            f"/menu/modifiers/{butter['id']}", json={"price_delta_cents": 150}, headers=ADMIN
        )
        assert priced.json()["price_delta_cents"] == 150

        changes = client.get(
            f"/locations/{venue.location_id}/menu/changes", headers=ADMIN
        ).json()
        assert [(c["entity"], c["action"]) for c in changes[:2]] == [
            ("modifier", "update"),
            ("modifier_group", "update"),
        ]
        for change in changes[:2]:
            undo = client.post(f"/menu/changes/{change['id']}/revert", headers=ADMIN)
            assert undo.status_code == 200, undo.text
        [extras] = _menu(client, venue)["modifier_groups"]
        assert (extras["max_select"], extras["available_from"]) == (3, None)
        assert extras["modifiers"][0]["price_delta_cents"] == 100

        unknown = client.patch("/menu/modifiers/01JXXXXXXXXXXXXXXXXXXXXXXX", json={}, headers=ADMIN)
        assert unknown.status_code == 404
        till = client.patch(
            f"/menu/modifiers/{butter['id']}", json={"name": "x"}, headers=auth(venue.token)
        )
        assert till.status_code == 403


# --- modifier import ---------------------------------------------------------------

SHEET = Path(__file__).resolve().parents[1].joinpath("docs", "modifier-template.csv").read_text(
    encoding="utf-8"
)


@pytest.fixture
def menu() -> Iterator[tuple[Session, str]]:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="Madras Curry House", name="Brisbane CBD")
        for name in ("Masala Dosa", "Chicken Curry", "Garlic Naan"):
            create_item(s, location_id=loc.id, name=name, base_price_cents=1500)
        s.commit()
        yield s, loc.id


def test_the_template_parses_into_groups() -> None:
    spice, extras = parse_modifier_csv(SHEET)
    assert (spice.name, spice.settings) == ("Spice level", {"min_select": 1, "max_select": 1})
    assert [m.name for m in spice.modifiers] == ["Mild", "Medium", "Hot"]
    assert spice.item_names == ["Masala Dosa"]
    assert [m.price_delta_cents for m in extras.modifiers] == [100, 50, 0]
    assert extras.modifiers[2].kitchen_name == "வெங்காயம் வேண்டாம்"


def test_every_bad_modifier_row_is_reported() -> None:
    sheet = (
        "group,modifier,price,min,max,available_from\n"
        "Spice,Mild,0,1,1,\n"
        "Spice,Hot,abc,2,,\n"
        "Spice,mild,0,,,\n"
        ",Orphan,0,,,\n"
        "Extras,Butter,1,3,2,\n"
        "Late,Chilli,0,,,25:00\n"
    )
    with pytest.raises(InvalidRequest) as exc:
        parse_modifier_csv(sheet)
    problems = exc.value.extra["problems"]
    assert problems == [
        "row 3: min for 'Spice' is 1 on row 2, not 2",
        "row 4: 'mild' is already in 'Spice' on row 2",
        "row 5: group and modifier are both needed",
        "row 7: available_from '25:00' isn't a time like 11:30",
        "row 6: max for 'Extras' is less than its min",
    ]


def test_import_creates_attaches_then_is_a_no_op(menu: tuple[Session, str]) -> None:
    s, loc = menu
    first = import_modifiers(s, loc, parse_modifier_csv(SHEET))
    assert first.created[:2] == ["Spice level", "Spice level: Mild"]
    assert len(first.created) == 8
    assert first.attached == ["Spice level -> Masala Dosa", "Extras -> Garlic Naan"]
    again = import_modifiers(s, loc, parse_modifier_csv(SHEET))
    assert (again.created, again.updated, again.attached) == ([], [], [])
    assert len(again.unchanged) == 8

    repriced = SHEET.replace("Extra butter,1.00", "Extra butter,1.20").replace(
        "Extras,0,3", "Extras,0,2"
    )
    third = import_modifiers(s, loc, parse_modifier_csv(repriced))
    assert third.updated == ["Extras", "Extras: Extra butter"]
    extras = next(g for g in list_modifier_groups(s, loc) if g.name == "Extras")
    assert extras.max_select == 2
    menu_now = till_menu(s, loc)
    dosa = next(e for e in menu_now.items if e.item.name == "Masala Dosa")
    assert len(dosa.modifier_group_ids) == 1


def test_import_refuses_unknown_items_and_dry_runs_change_nothing(
    menu: tuple[Session, str],
) -> None:
    s, loc = menu
    with pytest.raises(InvalidRequest, match="'Paneer Tikka', which isn't on the menu"):
        import_modifiers(s, loc, parse_modifier_csv("group,modifier,items\nX,Y,Paneer Tikka\n"))
    result = import_modifiers(s, loc, parse_modifier_csv(SHEET), dry_run=True)
    assert len(result.created) == 8 and len(result.attached) == 2
    assert list_modifier_groups(s, loc) == []


def test_import_modifiers_from_the_command_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="Madras Curry House", name="Brisbane CBD")
        for name in ("Masala Dosa", "Chicken Curry", "Garlic Naan"):
            create_item(s, location_id=loc.id, name=name, base_price_cents=1500)
        s.commit()
        loc_id = loc.id
    engine.dispose()
    sheet = tmp_path / "modifiers.csv"
    sheet.write_bytes(SHEET.encode("utf-8"))
    args = ["--database-url", url, "import-modifiers", "--location", loc_id, "--file", str(sheet)]

    assert main([*args, "--dry-run"]) == 0
    assert "Would create 8, update 0, attach 2, leave 0 unchanged." in capsys.readouterr().out
    assert main(args) == 0
    assert "Did create 8, update 0, attach 2" in capsys.readouterr().out
    assert main(args) == 0
    assert "Did create 0, update 0, attach 0, leave 8 unchanged." in capsys.readouterr().out


def test_import_checks_the_sheet_against_existing_groups_before_writing(
    menu: tuple[Session, str],
) -> None:
    s, loc = menu
    import_modifiers(s, loc, parse_modifier_csv("group,modifier,min\nExtras,Butter,2\n"))
    clash = parse_modifier_csv("group,modifier,max\nExtras,Butter,1\nExtras,Chutney,\n")
    for dry_run in (True, False):
        with pytest.raises(InvalidRequest, match="'Extras' would allow at most 1 but need 2"):
            import_modifiers(s, loc, clash, dry_run=dry_run)
    [extras] = list_modifier_groups(s, loc)
    assert (extras.min_select, extras.max_select) == (2, None)
