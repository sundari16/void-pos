"""Turn a spoken :class:`VoiceOrderDraft` into priced, menu-checked order lines.

This is the half of voice intake that the open core owns: the caller's words ("two masala
dosa, extra hot") arrive as names (or ids the stack already matched), and we resolve each
against the *live* menu for the location — names to menu items and modifiers, prices and
GST snapshotted from the catalogue, modifiers held to the groups their item allows and to
their min/max selection rules (via the same :func:`check_order_lines` the till path uses).

The result is a list of :class:`OrderLineIn` ready for ``place_order`` plus a
:class:`VoiceOrderConfirmation` for the read-back. Nothing is written here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest
from ..events.payloads import OrderLineIn
from ..ids import new_ulid
from ..menu.service import list_items
from ..models import Location, MenuItem, MenuItemModifierGroup, Modifier
from ..orders.rules import check_order_lines
from ..totals import order_math
from .agent import (
    ConfirmedItem,
    ConfirmedModifier,
    DraftItem,
    DraftModifier,
    VoiceOrderConfirmation,
    VoiceOrderDraft,
)


@dataclass(frozen=True)
class ResolvedOrder:
    """What a draft resolves to: lines to place, and the summary to read back."""

    lines: list[OrderLineIn]
    confirmation: VoiceOrderConfirmation


def resolve_draft(
    session: Session, location: Location, draft: VoiceOrderDraft, *, order_id: str
) -> ResolvedOrder:
    """Resolve ``draft`` against ``location``'s live menu, price it, and check it the way a
    till order is checked. Raises :class:`InvalidRequest` / :class:`Conflict` naming the
    first item or modifier that can't be resolved or isn't allowed."""
    if not draft.items:
        raise InvalidRequest("a voice order needs at least one item", code="empty_order")

    items_by_name = _items_by_name(session, location.id)
    lines: list[OrderLineIn] = []
    confirmed: list[ConfirmedItem] = []

    for draft_item in draft.items:
        item = _resolve_item(session, location, draft_item, items_by_name)
        item_line = OrderLineIn(
            id=new_ulid(),
            menu_item_id=item.id,
            quantity=draft_item.quantity,
            name_snapshot=item.name,
            kitchen_name_snapshot=item.kitchen_name,
            unit_price_cents=item.base_price_cents,
            tax_rate_snapshot=float(item.tax_rate),
            category_snapshot=item.category,
        )
        lines.append(item_line)

        allowed = _allowed_modifiers(session, item.id)
        confirmed_mods: list[ConfirmedModifier] = []
        for draft_mod in draft_item.modifiers:
            modifier = _resolve_modifier(item, draft_mod, allowed)
            lines.append(
                OrderLineIn(
                    id=new_ulid(),
                    parent_line_id=item_line.id,
                    modifier_id=modifier.id,
                    quantity=draft_mod.quantity,
                    name_snapshot=modifier.name,
                    kitchen_name_snapshot=modifier.kitchen_name,
                    unit_price_cents=modifier.price_delta_cents,
                    tax_rate_snapshot=float(item.tax_rate),
                )
            )
            confirmed_mods.append(
                ConfirmedModifier(
                    name=modifier.name,
                    quantity=draft_mod.quantity,
                    price_delta_cents=modifier.price_delta_cents,
                )
            )

        line_total = item.base_price_cents * draft_item.quantity + sum(
            m.price_delta_cents * m.quantity * draft_item.quantity for m in confirmed_mods
        )
        confirmed.append(
            ConfirmedItem(
                name=item.name,
                quantity=draft_item.quantity,
                unit_price_cents=item.base_price_cents,
                line_total_cents=line_total,
                modifiers=confirmed_mods,
            )
        )

    # The same live-menu checks a till order gets: item on sale, modifiers allowed and in
    # window, and each group's required min / allowed max selections satisfied.
    check_order_lines(session, location, lines)

    money = order_math(lines)
    confirmation = VoiceOrderConfirmation(
        order_id=order_id,
        customer_phone=draft.customer_phone,
        fulfillment=draft.fulfillment,
        items=confirmed,
        subtotal_cents=money.subtotal_cents,
        total_cents=money.total_cents,
        gst_cents=money.gst_cents,
        customer_name=draft.customer_name,
        note=draft.note,
        delivery_address=draft.delivery_address,
    )
    return ResolvedOrder(lines=lines, confirmation=confirmation)


# --- item resolution ------------------------------------------------------------------


def _items_by_name(session: Session, location_id: str) -> dict[str, list[MenuItem]]:
    by_name: dict[str, list[MenuItem]] = {}
    for item in list_items(session, location_id):
        by_name.setdefault(_norm(item.name), []).append(item)
    return by_name


def _resolve_item(
    session: Session,
    location: Location,
    draft_item: DraftItem,
    items_by_name: dict[str, list[MenuItem]],
) -> MenuItem:
    if draft_item.menu_item_id is not None:
        item = session.get(MenuItem, draft_item.menu_item_id)
        if item is None or item.location_id != location.id or item.deleted_at is not None:
            raise InvalidRequest(
                f"menu item {draft_item.menu_item_id} isn't on this venue's menu",
                code="item_not_found",
                menu_item_id=draft_item.menu_item_id,
            )
        return item
    if not draft_item.name or not draft_item.name.strip():
        raise InvalidRequest("an item needs a name or a menu id", code="item_unnamed")
    matches = _match_by_name(draft_item.name, items_by_name)
    if not matches:
        raise InvalidRequest(
            f"nothing on the menu matches {draft_item.name!r}",
            code="item_not_found",
            query=draft_item.name,
        )
    if len(matches) > 1:
        raise InvalidRequest(
            f"{draft_item.name!r} matches more than one item: "
            f"{', '.join(sorted(m.name for m in matches))}",
            code="item_ambiguous",
            query=draft_item.name,
            options=sorted(m.name for m in matches),
        )
    return matches[0]


def _match_by_name(query: str, by_name: dict[str, list[MenuItem]]) -> list[MenuItem]:
    """Exact (case-insensitive) match, else a unique substring match — so "dosa" finds
    "Masala Dosa" only when it is the one item whose name contains it."""
    norm = _norm(query)
    if norm in by_name:
        return by_name[norm]
    contains = [item for name, items in by_name.items() if norm in name for item in items]
    return contains


# --- modifier resolution --------------------------------------------------------------


def _allowed_modifiers(session: Session, menu_item_id: str) -> list[Modifier]:
    """Every modifier in a group attached to this item — what the caller may choose."""
    return list(
        session.scalars(
            select(Modifier)
            .join(
                MenuItemModifierGroup,
                MenuItemModifierGroup.modifier_group_id == Modifier.group_id,
            )
            .where(MenuItemModifierGroup.menu_item_id == menu_item_id)
            .order_by(Modifier.id)
        )
    )


def _resolve_modifier(
    item: MenuItem, draft_mod: DraftModifier, allowed: list[Modifier]
) -> Modifier:
    by_id = {m.id: m for m in allowed}
    if draft_mod.modifier_id is not None:
        modifier = by_id.get(draft_mod.modifier_id)
        if modifier is None:
            raise InvalidRequest(
                f"modifier {draft_mod.modifier_id} isn't a choice for {item.name}",
                code="modifier_not_allowed",
                modifier_id=draft_mod.modifier_id,
                menu_item_id=item.id,
            )
        return modifier
    if not draft_mod.name or not draft_mod.name.strip():
        raise InvalidRequest("a modifier needs a name or a modifier id", code="modifier_unnamed")
    matches = _match_modifier_by_name(draft_mod.name, allowed)
    if not matches:
        raise InvalidRequest(
            f"{item.name} has no option matching {draft_mod.name!r}",
            code="modifier_not_found",
            query=draft_mod.name,
            menu_item_id=item.id,
        )
    if len(matches) > 1:
        raise InvalidRequest(
            f"{draft_mod.name!r} matches more than one option for {item.name}: "
            f"{', '.join(sorted(m.name for m in matches))}",
            code="modifier_ambiguous",
            query=draft_mod.name,
            options=sorted(m.name for m in matches),
        )
    return matches[0]


def _match_modifier_by_name(query: str, allowed: list[Modifier]) -> list[Modifier]:
    norm = _norm(query)
    exact = [m for m in allowed if _norm(m.name) == norm]
    if exact:
        return exact
    return [m for m in allowed if norm in _norm(m.name)]


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()
