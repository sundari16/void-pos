from __future__ import annotations


class EventType:
    """The event vocabulary the order pipeline understands."""

    ORDER_PLACED = "order.placed"
    ORDER_LINES_ADDED = "order.lines_added"  # more items onto an open order
    ORDER_LINES_REMOVED = "order.lines_removed"  # items taken off an open order
    ORDER_ADJUSTED = "order.adjusted"  # a discount or surcharge on the order
    ORDER_ADJUSTMENT_REMOVED = "order.adjustment_removed"
    ORDER_REFUNDED = "order.refunded"  # money given back against a payment
    ORDER_SENT = "order.sent"
    ORDER_PAID = "order.paid"
    ORDER_VOIDED = "order.voided"
    LOYALTY_RECORDED = "loyalty.recorded"


# Order state machine.
OPEN = "open"
SENT = "sent"
PAID = "paid"
VOIDED = "voided"
REFUNDED = "refunded"  # paid, then every cent given back
TERMINAL = frozenset({PAID, VOIDED, REFUNDED})
UNSETTLED = frozenset({OPEN, SENT})

_ALLOWED: dict[str, frozenset[str]] = {
    OPEN: frozenset({SENT, PAID, VOIDED}),
    SENT: frozenset({PAID, VOIDED}),
    PAID: frozenset({REFUNDED}),
    VOIDED: frozenset(),
    REFUNDED: frozenset(),
}


def can_transition(current: str, target: str) -> bool:
    """True if `current -> target` is a legal order-state transition."""
    return target in _ALLOWED.get(current, frozenset())
