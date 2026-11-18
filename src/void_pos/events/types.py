from __future__ import annotations


class EventType:
    """The event vocabulary the order pipeline understands."""

    ORDER_PLACED = "order.placed"
    ORDER_LINES_ADDED = "order.lines_added"  # more items onto an open order
    ORDER_SENT = "order.sent"
    ORDER_PAID = "order.paid"
    ORDER_VOIDED = "order.voided"
    LOYALTY_RECORDED = "loyalty.recorded"


# Order state machine.
OPEN = "open"
SENT = "sent"
PAID = "paid"
VOIDED = "voided"
TERMINAL = frozenset({PAID, VOIDED})

_ALLOWED: dict[str, frozenset[str]] = {
    OPEN: frozenset({SENT, PAID, VOIDED}),
    SENT: frozenset({PAID, VOIDED}),
    PAID: frozenset(),
    VOIDED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """True if `current -> target` is a legal order-state transition."""
    return target in _ALLOWED.get(current, frozenset())
