"""Bring a venue's menu over from Square: items, prices, categories, GST and modifier sets.

It reads the Square item library through the Catalog API with an access token (read
only), turns it into the same rows the spreadsheet imports use, and loads them through
those imports, so it's matched by name, idempotent, recorded in the menu history and
revertible. Run it again after a price change in Square.

How Square maps:
- an item with one variation is one menu item; with several, one per variation,
  named "Item (Variation)". Variable-priced variations have no price and are skipped.
- the item's category (first category, else its reporting category) is the category.
- an enabled tax on the item sets the GST rate. Australian Square taxes are GST
  inclusive; an additive tax is reported, as menu prices here include GST.
- a modifier list is a modifier group. "Choose one" lists allow one; the minimum is the
  highest minimum any item asks for. The group is attached to every item that uses it.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx

from ..errors import InvalidRequest, ServiceUnavailable
from .importer import MenuRow
from .modifier_import import GroupRows, ModifierRow

API = "https://connect.squareup.com/v2"
SQUARE_VERSION = "2024-07-17"
TYPES = "ITEM,CATEGORY,MODIFIER_LIST,TAX"


class SquareCatalog:
    """Reads the item library. The token is only sent to Square, never logged."""

    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=API, timeout=30)
        self._headers = {"Authorization": f"Bearer {token}", "Square-Version": SQUARE_VERSION}

    def objects(self) -> Iterator[dict[str, Any]]:
        cursor: str | None = None
        while True:
            params = {"types": TYPES, **({"cursor": cursor} if cursor else {})}
            try:
                response = self._client.get("/catalog/list", params=params, headers=self._headers)
            except httpx.HTTPError as exc:
                raise ServiceUnavailable("can't reach Square", code="square_unavailable") from exc
            body: dict[str, Any] = response.json()
            if response.status_code == 401:
                raise InvalidRequest(
                    "Square refused the access token; check VOIDPOS_SQUARE_ACCESS_TOKEN",
                    code="square_unauthorized",
                )
            if response.status_code >= 400:
                errors = body.get("errors") or [{}]
                raise ServiceUnavailable(
                    f"Square: {errors[0].get('detail', 'request failed')}", code="square_error"
                )
            yield from body.get("objects", [])
            cursor = body.get("cursor")
            if not cursor:
                return


@dataclass
class SquareMenu:
    items: list[MenuRow] = field(default_factory=list)
    groups: list[GroupRows] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # what was skipped or needs a look


def _cents(money: dict[str, Any] | None) -> int | None:
    if not money or money.get("amount") is None:
        return None
    return int(money["amount"])


def translate(objects: list[dict[str, Any]]) -> SquareMenu:
    """Square catalog objects to import rows."""
    menu = SquareMenu()
    live = [o for o in objects if not o.get("is_deleted")]
    categories = {
        o["id"]: o.get("category_data", {}).get("name") for o in live if o["type"] == "CATEGORY"
    }
    taxes = {o["id"]: o.get("tax_data", {}) for o in live if o["type"] == "TAX"}
    lists = {o["id"]: o.get("modifier_list_data", {}) for o in live if o["type"] == "MODIFIER_LIST"}

    group_items: dict[str, list[str]] = {}
    group_min: dict[str, int] = {}
    taken: set[str] = set()
    for item in (o for o in live if o["type"] == "ITEM"):
        data = item.get("item_data", {})
        if data.get("is_archived"):
            continue
        name = (data.get("name") or "").strip()
        if not name:
            continue
        cats = data.get("categories") or []
        category_id = (cats[0].get("id") if cats else None) or data.get("category_id") or (
            (data.get("reporting_category") or {}).get("id")
        )
        category = categories.get(category_id) if category_id else None
        rate = 0.0
        for tax_id in data.get("tax_ids") or []:
            tax = taxes.get(tax_id)
            if not tax or tax.get("enabled") is False:
                continue
            rate = float(Decimal(str(tax.get("percentage") or "0")) / 100)
            if tax.get("inclusion_type") == "ADDITIVE":
                menu.notes.append(
                    f"{name}: Square adds {tax.get('name', 'tax')} on top; here prices include "
                    "GST, so check its price"
                )
        variations = [
            v for v in data.get("variations") or [] if not v.get("is_deleted")
        ]
        names_here: list[str] = []
        for variation in variations:
            vdata = variation.get("item_variation_data", {})
            price = _cents(vdata.get("price_money"))
            vname = (vdata.get("name") or "").strip()
            label = name if len(variations) == 1 or not vname else f"{name} ({vname})"
            if price is None or vdata.get("pricing_type") == "VARIABLE_PRICING":
                menu.notes.append(f"{label}: variable price in Square; skipped")
                continue
            if label.casefold() in taken and category:
                label = f"{label} - {category}"
            if label.casefold() in taken:
                menu.notes.append(f"{label}: the same name twice in Square; kept the first")
                continue
            taken.add(label.casefold())
            menu.items.append(
                MenuRow(
                    line=0,
                    name=label[:200],
                    base_price_cents=price,
                    category=(category or None) and category[:120],
                    kitchen_name=None,
                    tax_rate=rate,
                    is_available=True,
                )
            )
            names_here.append(label[:200])
        for info in data.get("modifier_list_info") or []:
            if info.get("enabled") is False or info.get("modifier_list_id") not in lists:
                continue
            list_id = info["modifier_list_id"]
            group_items.setdefault(list_id, []).extend(names_here)
            wanted = info.get("min_selected_modifiers")
            if isinstance(wanted, int) and wanted > 0:
                group_min[list_id] = max(group_min.get(list_id, 0), wanted)

    seen_groups: set[str] = set()
    for list_id, data in lists.items():
        name = (data.get("name") or "").strip()
        if not name or name.casefold() in seen_groups:
            continue
        seen_groups.add(name.casefold())
        modifiers = []
        seen_mods: set[str] = set()
        for mod in data.get("modifiers") or []:
            if mod.get("is_deleted"):
                continue
            mdata = mod.get("modifier_data", {})
            mname = (mdata.get("name") or "").strip()
            if not mname or mname.casefold() in seen_mods:
                continue
            seen_mods.add(mname.casefold())
            modifiers.append(
                ModifierRow(
                    line=0,
                    name=mname[:200],
                    price_delta_cents=_cents(mdata.get("price_money")) or 0,
                    kitchen_name=None,
                )
            )
        if not modifiers:
            continue
        settings: dict[str, Any] = {"min_select": group_min.get(list_id, 0)}
        if data.get("selection_type") == "SINGLE":
            settings["max_select"] = 1
        elif isinstance(data.get("max_selected_modifiers"), int) and data["max_selected_modifiers"] > 0:
            settings["max_select"] = data["max_selected_modifiers"]
        if "max_select" in settings and settings["max_select"] < settings["min_select"]:
            settings["max_select"] = settings["min_select"]
        menu.groups.append(
            GroupRows(
                line=0,
                name=name[:200],
                settings=settings,
                modifiers=modifiers,
                item_names=sorted(set(group_items.get(list_id, []))),
            )
        )
    return menu
