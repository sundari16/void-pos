"""Menu management: CRUD for items, modifier groups and modifiers.

The menu is server-authoritative reference data. True delete is safe because every order
line snapshots the name/price/tax/category at sale time, so removing an item never
corrupts order history — we simply null the (now redundant) line references. Every edit
is recorded in the menu's change history and the latest one can be reverted (see
``history``). Offline menu edits are a later refinement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import local_time
from ..errors import InvalidRequest, NotFound
from ..models import Location, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from .availability import check_window, in_window
from .history import (
    GROUP_FIELDS,
    ITEM_FIELDS,
    MODIFIER_FIELDS,
    group_snapshot,
    group_values,
    item_snapshot,
    item_values,
    modifier_snapshot,
    modifier_values,
    record,
    remove_item_row,
)


def create_item(
    session: Session,
    *,
    location_id: str,
    name: str,
    base_price_cents: int,
    kitchen_name: str | None = None,
    category: str | None = None,
    tax_rate: float = 0,
    is_available: bool = True,
    available_from: time | None = None,
    available_to: time | None = None,
) -> MenuItem:
    _require_location(session, location_id)
    check_window(available_from, available_to)
    item = MenuItem(
        location_id=location_id,
        name=name,
        base_price_cents=base_price_cents,
        kitchen_name=kitchen_name,
        category=category,
        tax_rate=tax_rate,
        is_available=is_available,
        available_from=available_from,
        available_to=available_to,
    )
    session.add(item)
    session.flush()
    record(
        session,
        location_id=location_id,
        entity="menu_item",
        entity_id=item.id,
        action="create",
        after=item_snapshot(session, item),
    )
    return item


def _require_location(session: Session, location_id: str) -> None:
    if session.get(Location, location_id) is None:
        raise NotFound("location not found")


def get_item(session: Session, item_id: str) -> MenuItem:
    item = session.get(MenuItem, item_id)
    if item is None:
        raise NotFound("menu item not found")
    return item


def list_items(session: Session, location_id: str, *, include_deleted: bool = False) -> list[MenuItem]:
    stmt = select(MenuItem).where(MenuItem.location_id == location_id)
    if not include_deleted:
        stmt = stmt.where(MenuItem.deleted_at.is_(None))
    return list(session.scalars(stmt.order_by(MenuItem.name)).all())


def update_item(session: Session, item_id: str, fields: dict[str, Any]) -> MenuItem:
    """Apply the given fields; only the ones that actually change are recorded."""
    item = get_item(session, item_id)
    _update(session, item, "menu_item", item.location_id, fields, ITEM_FIELDS, item_values)
    return item


def _update(
    session: Session,
    row: Any,
    entity: str,
    location_id: str,
    fields: dict[str, Any],
    allowed: tuple[str, ...],
    values: Callable[[Any], dict[str, Any]],
) -> None:
    """Apply ``fields`` to a menu row, check the result, and record what changed."""
    old = values(row)
    for key, value in fields.items():
        if key in allowed:
            setattr(row, key, value)
    if "available_from" in allowed:
        check_window(row.available_from, row.available_to)
    if entity == "modifier_group":
        _check_limits(row.min_select, row.max_select)
    session.flush()
    new = values(row)
    changed = [field for field in allowed if old[field] != new[field]]
    if changed:
        record(
            session,
            location_id=location_id,
            entity=entity,
            entity_id=row.id,
            action="update",
            before={field: old[field] for field in changed},
            after={field: new[field] for field in changed},
        )


def delete_item(session: Session, item_id: str) -> None:
    """True delete: history is preserved by the order-line snapshots, so null the line
    references and remove the item for real (not a soft hide). Revertible."""
    item = get_item(session, item_id)
    before = item_snapshot(session, item)
    remove_item_row(session, item)
    record(
        session,
        location_id=before["location_id"],
        entity="menu_item",
        entity_id=item_id,
        action="delete",
        before=before,
    )


def _check_limits(min_select: int, max_select: int | None) -> None:
    if min_select < 0 or (max_select is not None and max_select < min_select):
        raise InvalidRequest(
            "max_select must be at least min_select, and min_select at least 0",
            code="invalid_selection_limits",
        )


def create_modifier_group(
    session: Session,
    *,
    location_id: str,
    name: str,
    min_select: int = 0,
    max_select: int | None = None,
    available_from: time | None = None,
    available_to: time | None = None,
) -> ModifierGroup:
    _require_location(session, location_id)
    _check_limits(min_select, max_select)
    check_window(available_from, available_to)
    group = ModifierGroup(
        location_id=location_id,
        name=name,
        min_select=min_select,
        max_select=max_select,
        available_from=available_from,
        available_to=available_to,
    )
    session.add(group)
    session.flush()
    record(
        session,
        location_id=location_id,
        entity="modifier_group",
        entity_id=group.id,
        action="create",
        after=group_snapshot(group),
    )
    return group


def get_modifier_group(session: Session, group_id: str) -> ModifierGroup:
    group = session.get(ModifierGroup, group_id)
    if group is None:
        raise NotFound("modifier group not found")
    return group


def update_modifier_group(session: Session, group_id: str, fields: dict[str, Any]) -> ModifierGroup:
    group = get_modifier_group(session, group_id)
    _update(session, group, "modifier_group", group.location_id, fields, GROUP_FIELDS, group_values)
    return group


def get_modifier(session: Session, modifier_id: str) -> Modifier:
    modifier = session.get(Modifier, modifier_id)
    if modifier is None:
        raise NotFound("modifier not found")
    return modifier


def update_modifier(session: Session, modifier_id: str, fields: dict[str, Any]) -> Modifier:
    modifier = get_modifier(session, modifier_id)
    location_id = get_modifier_group(session, modifier.group_id).location_id
    _update(session, modifier, "modifier", location_id, fields, MODIFIER_FIELDS, modifier_values)
    return modifier


def list_modifier_groups(session: Session, location_id: str) -> list[ModifierGroup]:
    return list(
        session.scalars(
            select(ModifierGroup)
            .where(ModifierGroup.location_id == location_id)
            .order_by(ModifierGroup.name)
        ).all()
    )


def create_modifier(
    session: Session,
    *,
    group_id: str,
    name: str,
    price_delta_cents: int = 0,
    kitchen_name: str | None = None,
) -> Modifier:
    group = get_modifier_group(session, group_id)
    modifier = Modifier(
        group_id=group_id, name=name, price_delta_cents=price_delta_cents, kitchen_name=kitchen_name
    )
    session.add(modifier)
    session.flush()
    record(
        session,
        location_id=group.location_id,
        entity="modifier",
        entity_id=modifier.id,
        action="create",
        after=modifier_snapshot(modifier),
    )
    return modifier


def attach_modifier_group(session: Session, *, menu_item_id: str, modifier_group_id: str) -> None:
    item = get_item(session, menu_item_id)
    group = session.get(ModifierGroup, modifier_group_id)
    if group is None:
        raise NotFound("modifier group not found")
    if group.location_id != item.location_id:
        raise InvalidRequest(
            "the modifier group belongs to another location", code="location_mismatch"
        )
    if session.get(MenuItemModifierGroup, (menu_item_id, modifier_group_id)) is None:
        session.add(
            MenuItemModifierGroup(menu_item_id=menu_item_id, modifier_group_id=modifier_group_id)
        )
        session.flush()
        record(
            session,
            location_id=item.location_id,
            entity="menu_item",
            entity_id=item.id,
            action="attach",
            after={"modifier_group_id": modifier_group_id},
        )


# --- the till's menu ----------------------------------------------------------------


@dataclass(frozen=True)
class TillItem:
    item: MenuItem
    modifier_group_ids: list[str]
    available_now: bool


@dataclass(frozen=True)
class TillGroup:
    group: ModifierGroup
    modifiers: list[Modifier]
    available_now: bool


@dataclass(frozen=True)
class TillMenu:
    location: Location
    local_time: time
    items: list[TillItem]
    groups: list[TillGroup]


def till_menu(session: Session, location_id: str, at: datetime | None = None) -> TillMenu:
    """Everything a till needs to ring up an order: live items with the modifier groups
    that apply to each, every group with its modifiers (in the order they were added),
    and whether each item and group can be sold now in the location's local time."""
    location = session.get(Location, location_id)
    if location is None:
        raise NotFound("location not found")
    now = local_time(location.timezone, at)
    items = list_items(session, location_id)
    links: dict[str, list[str]] = {}
    for item_id, group_id in session.execute(
        select(MenuItemModifierGroup.menu_item_id, MenuItemModifierGroup.modifier_group_id)
        .join(ModifierGroup, ModifierGroup.id == MenuItemModifierGroup.modifier_group_id)
        .where(ModifierGroup.location_id == location_id)
        .order_by(ModifierGroup.name)
    ):
        links.setdefault(item_id, []).append(group_id)
    groups = list_modifier_groups(session, location_id)
    modifiers: dict[str, list[Modifier]] = {}
    for modifier in session.scalars(
        select(Modifier)
        .join(ModifierGroup, ModifierGroup.id == Modifier.group_id)
        .where(ModifierGroup.location_id == location_id)
        .order_by(Modifier.id)
    ):
        modifiers.setdefault(modifier.group_id, []).append(modifier)
    return TillMenu(
        location=location,
        local_time=now,
        items=[
            TillItem(
                item=item,
                modifier_group_ids=links.get(item.id, []),
                available_now=item.is_available
                and in_window(item.available_from, item.available_to, now),
            )
            for item in items
        ],
        groups=[
            TillGroup(
                group=group,
                modifiers=modifiers.get(group.id, []),
                available_now=in_window(group.available_from, group.available_to, now),
            )
            for group in groups
        ],
    )
