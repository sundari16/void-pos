"""Menu management: CRUD for items, modifier groups and modifiers.

The menu is server-authoritative reference data. True delete is safe because every order
line snapshots the name/price/tax/category at sale time, so removing an item never
corrupts order history — we simply null the (now redundant) line references. Every edit
is recorded in the menu's change history and the latest one can be reverted (see
``history``). Offline menu edits are a later refinement.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest, NotFound
from ..models import Location, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup
from .history import (
    ITEM_FIELDS,
    group_snapshot,
    item_snapshot,
    item_values,
    modifier_snapshot,
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
) -> MenuItem:
    _require_location(session, location_id)
    item = MenuItem(
        location_id=location_id,
        name=name,
        base_price_cents=base_price_cents,
        kitchen_name=kitchen_name,
        category=category,
        tax_rate=tax_rate,
        is_available=is_available,
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
    old = item_values(item)
    for key, value in fields.items():
        if key in ITEM_FIELDS:
            setattr(item, key, value)
    session.flush()
    new = item_values(item)
    changed = [field for field in ITEM_FIELDS if old[field] != new[field]]
    if changed:
        record(
            session,
            location_id=item.location_id,
            entity="menu_item",
            entity_id=item.id,
            action="update",
            before={field: old[field] for field in changed},
            after={field: new[field] for field in changed},
        )
    return item


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


def create_modifier_group(
    session: Session, *, location_id: str, name: str, min_select: int = 0, max_select: int | None = None
) -> ModifierGroup:
    _require_location(session, location_id)
    if min_select < 0 or (max_select is not None and max_select < min_select):
        raise InvalidRequest(
            "max_select must be at least min_select, and min_select at least 0",
            code="invalid_selection_limits",
        )
    group = ModifierGroup(
        location_id=location_id, name=name, min_select=min_select, max_select=max_select
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


def create_modifier(
    session: Session,
    *,
    group_id: str,
    name: str,
    price_delta_cents: int = 0,
    kitchen_name: str | None = None,
) -> Modifier:
    group = session.get(ModifierGroup, group_id)
    if group is None:
        raise NotFound("modifier group not found")
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
