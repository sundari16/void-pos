"""Menu management: item/modifier CRUD, with change history and revert."""

from __future__ import annotations

from .history import list_changes, revert_change
from .service import (
    attach_modifier_group,
    create_item,
    create_modifier,
    create_modifier_group,
    delete_item,
    get_item,
    get_modifier,
    get_modifier_group,
    list_items,
    list_modifier_groups,
    till_menu,
    update_item,
    update_modifier,
    update_modifier_group,
)

__all__ = [
    "attach_modifier_group",
    "create_item",
    "create_modifier",
    "create_modifier_group",
    "delete_item",
    "get_item",
    "get_modifier",
    "get_modifier_group",
    "list_changes",
    "list_items",
    "list_modifier_groups",
    "revert_change",
    "till_menu",
    "update_item",
    "update_modifier",
    "update_modifier_group",
]
