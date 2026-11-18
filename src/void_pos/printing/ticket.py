from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from ..models import Order, OrderLine


@dataclass
class TicketLine:
    text: str
    quantity: int
    is_modifier: bool


@dataclass
class KitchenTicket:
    order_number: int
    channel: str
    fulfillment: str
    business_date: date
    lines: list[TicketLine]
    void_reason: str | None = None  # set: this ticket cancels the order in the kitchen


def build_kitchen_ticket(
    order: Order, lines: Iterable[OrderLine], *, void_reason: str | None = None
) -> KitchenTicket:
    """Group modifier child lines under their parent item, preferring the kitchen-facing
    name (any script) over the customer-facing one."""
    children: dict[str, list[OrderLine]] = defaultdict(list)
    items: list[OrderLine] = []
    for ol in lines:
        if ol.parent_line_id is None:
            items.append(ol)
        else:
            children[ol.parent_line_id].append(ol)

    ticket_lines: list[TicketLine] = []
    for item in items:
        ticket_lines.append(
            TicketLine(
                text=item.kitchen_name_snapshot or item.name_snapshot,
                quantity=item.quantity,
                is_modifier=False,
            )
        )
        for mod in children.get(item.id, []):
            ticket_lines.append(
                TicketLine(
                    text=mod.kitchen_name_snapshot or mod.name_snapshot,
                    quantity=mod.quantity,
                    is_modifier=True,
                )
            )

    return KitchenTicket(
        order_number=order.order_number,
        channel=order.channel,
        fulfillment=order.fulfillment,
        business_date=order.business_date,
        lines=ticket_lines,
        void_reason=void_reason,
    )
