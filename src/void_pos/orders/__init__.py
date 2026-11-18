"""Order commands from a paired till."""

from __future__ import annotations

from .service import (
    KitchenOutcome,
    OrderOutcome,
    VoidOutcome,
    add_lines,
    adjust_order,
    get_order,
    place_order,
    remove_adjustment,
    remove_lines,
    send_to_kitchen,
    void_order,
)
from .views import UNSETTLED, OrderView, list_orders, order_view, order_views

__all__ = [
    "UNSETTLED",
    "KitchenOutcome",
    "OrderOutcome",
    "OrderView",
    "VoidOutcome",
    "add_lines",
    "adjust_order",
    "get_order",
    "list_orders",
    "order_view",
    "order_views",
    "place_order",
    "remove_adjustment",
    "remove_lines",
    "send_to_kitchen",
    "void_order",
]
