"""Menu change history with revert.

Every admin edit to the menu is recorded as a numbered change per location, with the
values before and after. Reverting works like an undo stack: only the latest change to an
item, modifier group or modifier can be reverted, so a revert never silently overwrites a
later edit, and a change can be reverted once. The revert is recorded as a change of its
own, so it can be undone in turn. Order history is untouched either way: order lines keep
their snapshots, so restoring a deleted item doesn't relink old orders to it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..errors import Conflict, InvalidRequest, NotFound
from ..models import MenuChange, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup, OrderLine

ITEM_FIELDS = ("name", "kitchen_name", "category", "base_price_cents", "tax_rate", "is_available")
_LABELS = {"menu_item": "item", "modifier_group": "modifier group", "modifier": "modifier"}


def item_values(item: MenuItem) -> dict[str, Any]:
    values = {field: getattr(item, field) for field in ITEM_FIELDS}
    values["tax_rate"] = float(item.tax_rate)
    return values


def item_snapshot(session: Session, item: MenuItem) -> dict[str, Any]:
    group_ids = session.scalars(
        select(MenuItemModifierGroup.modifier_group_id)
        .where(MenuItemModifierGroup.menu_item_id == item.id)
        .order_by(MenuItemModifierGroup.modifier_group_id)
    ).all()
    return {
        "id": item.id,
        "location_id": item.location_id,
        **item_values(item),
        "modifier_group_ids": list(group_ids),
    }


def group_snapshot(group: ModifierGroup) -> dict[str, Any]:
    return {
        "id": group.id,
        "location_id": group.location_id,
        "name": group.name,
        "min_select": group.min_select,
        "max_select": group.max_select,
    }


def modifier_snapshot(modifier: Modifier) -> dict[str, Any]:
    return {
        "id": modifier.id,
        "group_id": modifier.group_id,
        "name": modifier.name,
        "kitchen_name": modifier.kitchen_name,
        "price_delta_cents": modifier.price_delta_cents,
    }


def record(
    session: Session,
    *,
    location_id: str,
    entity: str,
    entity_id: str,
    action: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reverts: MenuChange | None = None,
) -> MenuChange:
    number = session.scalar(
        select(func.max(MenuChange.number)).where(MenuChange.location_id == location_id)
    )
    change = MenuChange(
        location_id=location_id,
        number=(number or 0) + 1,
        entity=entity,
        entity_id=entity_id,
        action=action,
        before=before,
        after=after,
        reverts_id=reverts.id if reverts is not None else None,
    )
    session.add(change)
    session.flush()
    return change


def list_changes(session: Session, location_id: str, *, limit: int = 50) -> list[MenuChange]:
    """Newest first."""
    return list(
        session.scalars(
            select(MenuChange)
            .where(MenuChange.location_id == location_id)
            .order_by(MenuChange.number.desc())
            .limit(limit)
        ).all()
    )


def remove_item_row(session: Session, item: MenuItem) -> None:
    """True delete: order lines keep their snapshots, so null their references."""
    session.execute(
        delete(MenuItemModifierGroup).where(MenuItemModifierGroup.menu_item_id == item.id)
    )
    session.execute(
        update(OrderLine).where(OrderLine.menu_item_id == item.id).values(menu_item_id=None)
    )
    session.delete(item)
    session.flush()


def remove_modifier_row(session: Session, modifier: Modifier) -> None:
    session.execute(
        update(OrderLine).where(OrderLine.modifier_id == modifier.id).values(modifier_id=None)
    )
    session.delete(modifier)
    session.flush()


# --- revert ------------------------------------------------------------------------


def revert_change(session: Session, change_id: str) -> MenuChange:
    """Undo one change and record the undo. Refuses anything but the latest change to
    its item, group or modifier, and a change that was already reverted."""
    change = session.get(MenuChange, change_id)
    if change is None:
        raise NotFound("menu change not found")
    if session.scalar(select(MenuChange.id).where(MenuChange.reverts_id == change.id)):
        raise Conflict(f"change #{change.number} was already reverted", code="already_reverted")
    latest = session.scalars(
        select(MenuChange)
        .where(
            MenuChange.location_id == change.location_id,
            MenuChange.entity == change.entity,
            MenuChange.entity_id == change.entity_id,
        )
        .order_by(MenuChange.number.desc())
        .limit(1)
    ).first()
    if latest is not None and latest.id != change.id:
        label = _LABELS.get(change.entity, change.entity)
        raise Conflict(
            f"change #{latest.number} to this {label} is newer; revert it first",
            code="changed_since",
            later_change=latest.number,
        )
    handler = _REVERTS.get((change.entity, change.action))
    if handler is None:
        raise InvalidRequest(
            f"a {change.action} of a {change.entity} can't be reverted", code="not_revertible"
        )
    return handler(session, change)


def _undo(change: MenuChange, session: Session, action: str, **values: Any) -> MenuChange:
    return record(
        session,
        location_id=change.location_id,
        entity=change.entity,
        entity_id=change.entity_id,
        action=action,
        reverts=change,
        **values,
    )


def _item(session: Session, change: MenuChange) -> MenuItem:
    item = session.get(MenuItem, change.entity_id)
    if item is None:
        raise NotFound("menu item not found")
    return item


def _revert_item_create(session: Session, change: MenuChange) -> MenuChange:
    item = _item(session, change)
    before = item_snapshot(session, item)
    remove_item_row(session, item)
    return _undo(change, session, "delete", before=before)


def _revert_item_update(session: Session, change: MenuChange) -> MenuChange:
    item = _item(session, change)
    restore = change.before or {}
    current = {field: item_values(item)[field] for field in restore}
    for field, value in restore.items():
        setattr(item, field, value)
    session.flush()
    return _undo(change, session, "update", before=current, after=restore)


def _revert_item_delete(session: Session, change: MenuChange) -> MenuChange:
    snap = change.before or {}
    item = MenuItem(
        id=snap["id"],
        location_id=snap["location_id"],
        **{field: snap[field] for field in ITEM_FIELDS},
    )
    session.add(item)
    session.flush()
    for group_id in snap.get("modifier_group_ids", []):
        if session.get(ModifierGroup, group_id) is not None:
            session.add(MenuItemModifierGroup(menu_item_id=item.id, modifier_group_id=group_id))
    session.flush()
    return _undo(change, session, "create", after=item_snapshot(session, item))


def _revert_item_attach(session: Session, change: MenuChange) -> MenuChange:
    group_id = (change.after or {})["modifier_group_id"]
    link = session.get(MenuItemModifierGroup, (change.entity_id, group_id))
    if link is not None:
        session.delete(link)
        session.flush()
    return _undo(change, session, "detach", before={"modifier_group_id": group_id})


def _revert_item_detach(session: Session, change: MenuChange) -> MenuChange:
    group_id = (change.before or {})["modifier_group_id"]
    _item(session, change)
    if session.get(ModifierGroup, group_id) is None:
        raise Conflict("that modifier group no longer exists", code="group_missing")
    if session.get(MenuItemModifierGroup, (change.entity_id, group_id)) is None:
        session.add(MenuItemModifierGroup(menu_item_id=change.entity_id, modifier_group_id=group_id))
        session.flush()
    return _undo(change, session, "attach", after={"modifier_group_id": group_id})


def _revert_group_create(session: Session, change: MenuChange) -> MenuChange:
    group = session.get(ModifierGroup, change.entity_id)
    if group is None:
        raise NotFound("modifier group not found")
    in_use = session.scalar(
        select(func.count()).select_from(Modifier).where(Modifier.group_id == group.id)
    ) or session.scalar(
        select(func.count())
        .select_from(MenuItemModifierGroup)
        .where(MenuItemModifierGroup.modifier_group_id == group.id)
    )
    if in_use:
        raise Conflict(
            "the group still has modifiers or items; revert those changes first", code="in_use"
        )
    before = group_snapshot(group)
    session.delete(group)
    session.flush()
    return _undo(change, session, "delete", before=before)


def _revert_group_delete(session: Session, change: MenuChange) -> MenuChange:
    group = ModifierGroup(**(change.before or {}))
    session.add(group)
    session.flush()
    return _undo(change, session, "create", after=group_snapshot(group))


def _revert_modifier_create(session: Session, change: MenuChange) -> MenuChange:
    modifier = session.get(Modifier, change.entity_id)
    if modifier is None:
        raise NotFound("modifier not found")
    before = modifier_snapshot(modifier)
    remove_modifier_row(session, modifier)
    return _undo(change, session, "delete", before=before)


def _revert_modifier_delete(session: Session, change: MenuChange) -> MenuChange:
    snap = change.before or {}
    if session.get(ModifierGroup, snap["group_id"]) is None:
        raise Conflict("that modifier's group no longer exists", code="group_missing")
    modifier = Modifier(**snap)
    session.add(modifier)
    session.flush()
    return _undo(change, session, "create", after=modifier_snapshot(modifier))


_REVERTS: dict[tuple[str, str], Callable[[Session, MenuChange], MenuChange]] = {
    ("menu_item", "create"): _revert_item_create,
    ("menu_item", "update"): _revert_item_update,
    ("menu_item", "delete"): _revert_item_delete,
    ("menu_item", "attach"): _revert_item_attach,
    ("menu_item", "detach"): _revert_item_detach,
    ("modifier_group", "create"): _revert_group_create,
    ("modifier_group", "delete"): _revert_group_delete,
    ("modifier", "create"): _revert_modifier_create,
    ("modifier", "delete"): _revert_modifier_delete,
}
