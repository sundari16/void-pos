from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from support import make_engine
from void_pos import cli
from void_pos.config import settings
from void_pos.devices import create_location
from void_pos.errors import InvalidRequest
from void_pos.menu import list_items, list_modifier_groups, till_menu
from void_pos.menu.importer import import_menu
from void_pos.menu.modifier_import import import_modifiers
from void_pos.menu.square import SquareCatalog, translate
from void_pos.models import Base


def _money(cents: int) -> dict[str, Any]:
    return {"amount": cents, "currency": "AUD"}


CATALOG: list[dict[str, Any]] = [
    {"type": "CATEGORY", "id": "C1", "category_data": {"name": "Mains"}},
    {"type": "CATEGORY", "id": "C2", "category_data": {"name": "Drinks"}},
    {"type": "TAX", "id": "T1", "tax_data": {"name": "GST", "percentage": "10",
                                            "inclusion_type": "INCLUSIVE", "enabled": True}},
    {"type": "MODIFIER_LIST", "id": "M1", "modifier_list_data": {
        "name": "Spice level", "selection_type": "SINGLE", "modifiers": [
            {"id": "m1", "modifier_data": {"name": "Mild"}},
            {"id": "m2", "modifier_data": {"name": "Hot", "price_money": _money(0)}},
        ]}},
    {"type": "MODIFIER_LIST", "id": "M2", "modifier_list_data": {
        "name": "Extras", "selection_type": "MULTIPLE", "modifiers": [
            {"id": "m3", "modifier_data": {"name": "Extra butter", "price_money": _money(150)}},
        ]}},
    {"type": "ITEM", "id": "I1", "item_data": {
        "name": "Masala Dosa", "categories": [{"id": "C1"}], "tax_ids": ["T1"],
        "variations": [{"id": "V1", "item_variation_data": {
            "name": "Regular", "price_money": _money(1850), "pricing_type": "FIXED_PRICING"}}],
        "modifier_list_info": [{"modifier_list_id": "M1", "min_selected_modifiers": 1},
                               {"modifier_list_id": "M2"}]}},
    {"type": "ITEM", "id": "I2", "item_data": {
        "name": "Mango Lassi", "category_id": "C2", "tax_ids": ["T1"], "variations": [
            {"id": "V2", "item_variation_data": {"name": "Small", "price_money": _money(550)}},
            {"id": "V3", "item_variation_data": {"name": "Large", "price_money": _money(750)}},
            {"id": "V4", "item_variation_data": {"name": "Jug", "pricing_type": "VARIABLE_PRICING"}},
        ]}},
    {"type": "ITEM", "id": "I3", "is_deleted": True, "item_data": {"name": "Gone"}},
    {"type": "ITEM", "id": "I4", "item_data": {"name": "Old special", "is_archived": True,
        "variations": [{"id": "V5", "item_variation_data": {"price_money": _money(100)}}]}},
]


def test_square_items_variations_tax_and_modifier_sets_translate() -> None:
    menu = translate(CATALOG)
    assert [(r.name, r.base_price_cents, r.category, r.tax_rate) for r in menu.items] == [
        ("Masala Dosa", 1850, "Mains", 0.1),
        ("Mango Lassi (Small)", 550, "Drinks", 0.1),
        ("Mango Lassi (Large)", 750, "Drinks", 0.1),
    ]
    spice, extras = menu.groups
    assert (spice.name, spice.settings, spice.item_names) == (
        "Spice level", {"min_select": 1, "max_select": 1}, ["Masala Dosa"],
    )
    assert [(m.name, m.price_delta_cents) for m in spice.modifiers] == [("Mild", 0), ("Hot", 0)]
    assert (extras.settings, extras.modifiers[0].price_delta_cents) == ({"min_select": 0}, 150)
    assert menu.notes == ["Mango Lassi (Jug): variable price in Square; skipped"]


def test_a_square_menu_loads_and_reloads_cleanly() -> None:
    with Session(make_engine()) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        menu = translate(CATALOG)
        first = import_menu(s, loc, menu.items)
        groups = import_modifiers(s, loc, menu.groups)
        assert (len(first.created), len(groups.attached)) == (3, 2)
        again = translate(CATALOG)
        assert import_menu(s, loc, again.items).created == []
        assert import_modifiers(s, loc, again.groups).attached == []
        dosa = next(e for e in till_menu(s, loc).items if e.item.name == "Masala Dosa")
        assert len(dosa.modifier_group_ids) == 2
        assert len(list_items(s, loc)) == 3 and len(list_modifier_groups(s, loc)) == 2


def test_the_catalog_client_pages_and_sends_the_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"objects": CATALOG[:3], "cursor": "next"})
        return httpx.Response(200, json={"objects": CATALOG[3:]})

    client = httpx.Client(base_url="https://connect.squareup.com/v2",
                          transport=httpx.MockTransport(handler))
    objects = list(SquareCatalog("EAAA_test", client).objects())
    assert len(objects) == len(CATALOG)
    assert seen[0].headers["Authorization"] == "Bearer EAAA_test"
    assert seen[0].headers["Square-Version"] and seen[1].url.params["cursor"] == "next"

    refused = httpx.Client(base_url="https://connect.squareup.com/v2", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, json={"errors": [{"detail": "bad token"}]})))
    with pytest.raises(InvalidRequest, match="VOIDPOS_SQUARE_ACCESS_TOKEN"):
        list(SquareCatalog("bad", refused).objects())


def test_import_square_from_the_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
    engine.dispose()

    class FakeCatalog:
        def __init__(self, token: str) -> None:
            assert token == "EAAA_test"

        def objects(self) -> list[dict[str, Any]]:
            return CATALOG

    args = ["--database-url", url, "import-square", "--location", loc]
    monkeypatch.setattr(settings, "square_access_token", None)
    assert cli.main(args) == 1
    assert "VOIDPOS_SQUARE_ACCESS_TOKEN" in capsys.readouterr().err

    from pydantic import SecretStr

    monkeypatch.setattr(settings, "square_access_token", SecretStr("EAAA_test"))
    monkeypatch.setattr(cli, "SquareCatalog", FakeCatalog)
    assert cli.main([*args, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Would create 3 items" in out and "! Mango Lassi (Jug)" in out
    assert cli.main(args) == 0
    assert "Did create 3 items" in capsys.readouterr().out
    assert cli.main(args) == 0
    assert "Did create 0 items, update 0, leave 3 unchanged." in capsys.readouterr().out
