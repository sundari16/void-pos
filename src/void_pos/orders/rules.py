"""Menu rules a till's new order must follow when it is placed online.

These run on ``POST /orders`` only. An order synced from a till that was offline already
happened at the counter, so the sync path records it as it was rung up.

- An item line naming a menu item needs that item on sale now: on the menu, switched on,
  and inside its daily window (``item_unavailable``, 409).
- A modifier line naming a catalog modifier needs that modifier's group to apply to its
  item (``modifier_not_allowed``, 422) and to be on sale now (``modifier_unavailable``, 409).
- Each group that applies to an item gets between ``min_select`` and ``max_select``
  choices on that item line, counting each modifier line's quantity
  (``modifier_selection``, 422). A group outside its window has no minimum.

Lines without a menu reference (an open item, a free-text note) are left alone.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import local_time
from ..errors import Conflict, InvalidRequest
from ..events.payloads import OrderLineIn
from ..menu.availability import in_window
from ..models import Location, MenuItem, MenuItemModifierGroup, Modifier, ModifierGroup


def check_order_lines(
    session: Session, location: Location, lines: list[OrderLineIn], at: datetime | None = None
) -> None:
    now = local_time(location.timezone, at)
    items_by_line = {line.id: line for line in lines if line.parent_line_id is None}

    item_ids = {line.menu_item_id for line in items_by_line.values() if line.menu_item_id}
    items = {
        item.id: item
        for item in session.scalars(
            select(MenuItem).where(
                MenuItem.id.in_(item_ids),
                MenuItem.location_id == location.id,
                MenuItem.deleted_at.is_(None),
            )
        )
    }
    for line in items_by_line.values():
        if line.menu_item_id is None:
            continue
        item = items.get(line.menu_item_id)
        if item is None or not item.is_available:
            raise Conflict(
                f"{line.name_snapshot} isn't on sale",
                code="item_unavailable",
                line_id=line.id,
                menu_item_id=line.menu_item_id,
            )
        if not in_window(item.available_from, item.available_to, now):
            raise Conflict(
                f"{line.name_snapshot} isn't on sale at {now:%H:%M}",
                code="item_unavailable",
                line_id=line.id,
                menu_item_id=line.menu_item_id,
            )

    groups_of: dict[str, set[str]] = defaultdict(set)
    for item_id, group_id in session.execute(
        select(MenuItemModifierGroup.menu_item_id, MenuItemModifierGroup.modifier_group_id).where(
            MenuItemModifierGroup.menu_item_id.in_(item_ids)
        )
    ):
        groups_of[item_id].add(group_id)
    modifier_ids = {line.modifier_id for line in lines if line.parent_line_id and line.modifier_id}
    modifiers = {
        m.id: m for m in session.scalars(select(Modifier).where(Modifier.id.in_(modifier_ids)))
    }
    group_ids = set().union(*groups_of.values()) | {m.group_id for m in modifiers.values()}
    groups = {
        g.id: g
        for g in session.scalars(
            select(ModifierGroup).where(
                ModifierGroup.id.in_(group_ids), ModifierGroup.location_id == location.id
            )
        )
    }

    chosen: dict[tuple[str, str], int] = defaultdict(int)  # (item line, group) -> count
    for line in lines:
        if line.parent_line_id is None or line.modifier_id is None:
            continue
        parent = items_by_line[line.parent_line_id]
        modifier = modifiers.get(line.modifier_id)
        group = groups.get(modifier.group_id) if modifier is not None else None
        if (
            group is None
            or parent.menu_item_id is None
            or group.id not in groups_of[parent.menu_item_id]
        ):
            raise InvalidRequest(
                f"{line.name_snapshot} isn't a choice for {parent.name_snapshot}",
                code="modifier_not_allowed",
                line_id=line.id,
                modifier_id=line.modifier_id,
            )
        if not in_window(group.available_from, group.available_to, now):
            raise Conflict(
                f"{line.name_snapshot} isn't available at {now:%H:%M}",
                code="modifier_unavailable",
                line_id=line.id,
                modifier_id=line.modifier_id,
            )
        chosen[parent.id, group.id] += line.quantity

    for line in items_by_line.values():
        if line.menu_item_id is None:
            continue
        for group_id in sorted(groups_of[line.menu_item_id]):
            group = groups.get(group_id)
            if group is None:
                continue
            count = chosen[line.id, group.id]
            if count < group.min_select and in_window(
                group.available_from, group.available_to, now
            ):
                raise InvalidRequest(
                    f"{line.name_snapshot} needs at least {group.min_select} from {group.name}",
                    code="modifier_selection",
                    line_id=line.id,
                    modifier_group_id=group.id,
                    min_select=group.min_select,
                    max_select=group.max_select,
                )
            if group.max_select is not None and count > group.max_select:
                raise InvalidRequest(
                    f"{line.name_snapshot} can have at most {group.max_select} from {group.name}",
                    code="modifier_selection",
                    line_id=line.id,
                    modifier_group_id=group.id,
                    min_select=group.min_select,
                    max_select=group.max_select,
                )
