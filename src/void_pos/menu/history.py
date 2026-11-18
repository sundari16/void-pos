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
from datetime import time
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..errors import Conflict, InvalidRequest, NotFound
from ..models import MenuChange, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup, OrderLine

ITEM_FIELDS = (
    "name",
    "kitchen_name",
    "category",
    "base_price_cents",
    "tax_rate",
    "is_available",
    "available_from",
    "available_to",
)
GROUP_FIELDS = ("name", "min_select", "max_select", "available_from", "available_to")
MODIFIER_FIELDS = ("name", "kitchen_name", "price_delta_cents")
_TIME_FIELDS = {"available_from", "available_to"}
_LABELS = {"menu_item": "item", "modifier_group": "modifier group", "modifier": "modifier"}


def _to_json(value: Any) -> Any:
    return value.isoformat() if isinstance(value, time) else value


def from_json(field: str, value: Any) -> Any:
    """A recorded value back as the model's type (times are stored as ``HH:MM:SS``)."""
    if field in _TIME_FIELDS and isinstance(value, str):
        return time.fromisoformat(value)
    return value


def _values(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _to_json(getattr(row, field)) for field in fields}


def item_values(item: MenuItem) -> dict[str, Any]:
    values = _values(item, ITEM_FIELDS)
    values["tax_rate"] = float(item.tax_rate)
    return values


def group_values(group: ModifierGroup) -> dict[str, Any]:
    return _values(group, GROUP_FIELDS)


def modifier_values(modifier: Modifier) -> dict[str, Any]:
    return _values(modifier, MODIFIER_FIELDS)


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
    return {"id": group.id, "location_id": group.location_id, **group_values(group)}


def modifier_snapshot(modifier: Modifier) -> dict[str, Any]:
    return {"id": modifier.id, "group_id": modifier.group_id, **modifier_values(modifier)}


def _restore(snap: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """A snapshot's field values for a new row; fields added since it was taken are unset."""
    return {field: from_json(field, snap[field]) for field in fields if field in snap}


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
    actor: str = "admin",
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
        actor=actor[:80],
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


_MODELS: dict[str, Any] = {
    "menu_item": (MenuItem, item_values),
    "modifier_group": (ModifierGroup, group_values),
    "modifier": (Modifier, modifier_values),
}


def _revert_update(session: Session, change: MenuChange) -> MenuChange:
    model, values = _MODELS[change.entity]
    row = session.get(model, change.entity_id)
    if row is None:
        raise NotFound(f"{_LABELS[change.entity]} not found")
    restore = change.before or {}
    now = values(row)
    current = {field: now[field] for field in restore}
    for field, value in restore.items():
        setattr(row, field, from_json(field, value))
    session.flush()
    return _undo(change, session, "update", before=current, after=restore)


def _revert_item_delete(session: Session, change: MenuChange) -> MenuChange:
    snap = change.before or {}
    item = MenuItem(id=snap["id"], location_id=snap["location_id"], **_restore(snap, ITEM_FIELDS))
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
    snap = change.before or {}
    group = ModifierGroup(
        id=snap["id"], location_id=snap["location_id"], **_restore(snap, GROUP_FIELDS)
    )
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
    modifier = Modifier(id=snap["id"], group_id=snap["group_id"], **_restore(snap, MODIFIER_FIELDS))
    session.add(modifier)
    session.flush()
    return _undo(change, session, "create", after=modifier_snapshot(modifier))


_REVERTS: dict[tuple[str, str], Callable[[Session, MenuChange], MenuChange]] = {
    ("menu_item", "create"): _revert_item_create,
    ("menu_item", "update"): _revert_update,
    ("menu_item", "delete"): _revert_item_delete,
    ("menu_item", "attach"): _revert_item_attach,
    ("menu_item", "detach"): _revert_item_detach,
    ("modifier_group", "create"): _revert_group_create,
    ("modifier_group", "update"): _revert_update,
    ("modifier_group", "delete"): _revert_group_delete,
    ("modifier", "create"): _revert_modifier_create,
    ("modifier", "update"): _revert_update,
    ("modifier", "delete"): _revert_modifier_delete,
}
