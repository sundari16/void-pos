"""Build a partner-neutral snapshot of a venue's live menu, for a provider to publish.

Reads the same catalog the till reads (``till_menu``): every live item with the modifier
groups that apply to it, every group with its options, and whether each can be sold right
now in the location's local time. A provider's ``build_menu`` turns this into its own menu
payload; nothing here knows any partner's shape. Prices are integer cents and include GST,
as everywhere in Void POS.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..menu import till_menu
from .provider import MenuSnapshot, SnapshotGroup, SnapshotItem, SnapshotModifier

CURRENCY = "AUD"  # Void POS is AUD-first; there is one currency per venue today.


def build_menu_snapshot(session: Session, location_id: str) -> MenuSnapshot:
    """The venue's catalog as a :class:`MenuSnapshot`. ``available`` is the item/group
    being sellable at the moment the snapshot is taken (live flag plus time window)."""
    menu = till_menu(session, location_id)
    items = [
        SnapshotItem(
            id=entry.item.id,
            name=entry.item.name,
            price_cents=entry.item.base_price_cents,
            category=entry.item.category,
            tax_rate=float(entry.item.tax_rate),
            available=entry.available_now,
            kitchen_name=entry.item.kitchen_name,
            modifier_group_ids=list(entry.modifier_group_ids),
        )
        for entry in menu.items
    ]
    groups = [
        SnapshotGroup(
            id=entry.group.id,
            name=entry.group.name,
            min_select=entry.group.min_select,
            max_select=entry.group.max_select,
            modifiers=[
                SnapshotModifier(
                    id=mod.id,
                    name=mod.name,
                    price_delta_cents=mod.price_delta_cents,
                    kitchen_name=mod.kitchen_name,
                )
                for mod in entry.modifiers
            ],
        )
        for entry in menu.groups
    ]
    return MenuSnapshot(location_id=location_id, currency=CURRENCY, items=items, groups=groups)
