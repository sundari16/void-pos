"""Menu management: CRUD for items, modifier groups and modifiers.

The menu is server-authoritative reference data. True delete is safe because every order
line snapshots the name/price/tax/category at sale time, so removing an item never
corrupts order history — we simply null the (now redundant) line references. Offline menu
edits and change-history/revert are later refinements.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from ..models import MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup, OrderLine

_ITEM_FIELDS = frozenset(
    {"name", "base_price_cents", "kitchen_name", "category", "tax_rate", "is_available"}
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
    return item


def get_item(session: Session, item_id: str) -> MenuItem:
    item = session.get(MenuItem, item_id)
    if item is None:
        raise LookupError(f"menu item {item_id!r} not found")
    return item


def list_items(session: Session, location_id: str, *, include_deleted: bool = False) -> list[MenuItem]:
    stmt = select(MenuItem).where(MenuItem.location_id == location_id)
    if not include_deleted:
        stmt = stmt.where(MenuItem.deleted_at.is_(None))
    return list(session.scalars(stmt.order_by(MenuItem.name)).all())


def update_item(session: Session, item_id: str, fields: dict[str, Any]) -> MenuItem:
    item = get_item(session, item_id)
    for key, value in fields.items():
        if key in _ITEM_FIELDS:
            setattr(item, key, value)
    session.flush()
    return item


def delete_item(session: Session, item_id: str) -> None:
    """True delete: history is preserved by the order-line snapshots, so null the line
    references and remove the item for real (not a soft hide)."""
    item = get_item(session, item_id)
    session.execute(
        delete(MenuItemModifierGroup).where(MenuItemModifierGroup.menu_item_id == item_id)
    )
    session.execute(
        update(OrderLine).where(OrderLine.menu_item_id == item_id).values(menu_item_id=None)
    )
    session.delete(item)
    session.flush()


def create_modifier_group(
    session: Session, *, location_id: str, name: str, min_select: int = 0, max_select: int | None = None
) -> ModifierGroup:
    group = ModifierGroup(
        location_id=location_id, name=name, min_select=min_select, max_select=max_select
    )
    session.add(group)
    session.flush()
    return group


def create_modifier(
    session: Session,
    *,
    group_id: str,
    name: str,
    price_delta_cents: int = 0,
    kitchen_name: str | None = None,
) -> Modifier:
    if session.get(ModifierGroup, group_id) is None:
        raise LookupError(f"modifier group {group_id!r} not found")
    modifier = Modifier(
        group_id=group_id, name=name, price_delta_cents=price_delta_cents, kitchen_name=kitchen_name
    )
    session.add(modifier)
    session.flush()
    return modifier


def attach_modifier_group(session: Session, *, menu_item_id: str, modifier_group_id: str) -> None:
    if session.get(MenuItem, menu_item_id) is None:
        raise LookupError(f"menu item {menu_item_id!r} not found")
    if session.get(ModifierGroup, modifier_group_id) is None:
        raise LookupError(f"modifier group {modifier_group_id!r} not found")
    if session.get(MenuItemModifierGroup, (menu_item_id, modifier_group_id)) is None:
        session.add(
            MenuItemModifierGroup(menu_item_id=menu_item_id, modifier_group_id=modifier_group_id)
        )
        session.flush()
