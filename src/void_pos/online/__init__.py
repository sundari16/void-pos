"""Customer online ordering: the storefront write path on the ``web`` channel.

A location's orders, whatever the channel, share one pipeline; the storefront drives it
through a server-side web device (see ``device``) instead of a paired till.
"""

from __future__ import annotations

from .device import WEB_ORDER_NUMBER_START, web_device
from .service import (
    WebOrderResult,
    get_web_order,
    pay_web_order_card,
    place_web_order,
    web_stripe_intent,
)

__all__ = [
    "WEB_ORDER_NUMBER_START",
    "WebOrderResult",
    "get_web_order",
    "pay_web_order_card",
    "place_web_order",
    "web_device",
    "web_stripe_intent",
]
