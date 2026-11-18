"""Order commands from a paired till."""

from __future__ import annotations

from .service import (
    KitchenOutcome,
    OrderOutcome,
    VoidOutcome,
    get_order,
    place_order,
    send_to_kitchen,
    void_order,
)

__all__ = [
    "KitchenOutcome",
    "OrderOutcome",
    "VoidOutcome",
    "get_order",
    "place_order",
    "send_to_kitchen",
    "void_order",
]
