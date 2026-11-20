"""On-demand delivery dispatch: a provider seam plus a built-in mock courier.

The open core dispatches a courier for an own-channel delivery order (pickup = venue,
dropoff = customer), tracks its status and cancels it, through a partner-neutral
``DeliveryProvider``. ``MockCourier`` is the open-core implementation; the real Uber Direct
adapter — which needs partner credentials — implements the same protocol in the commercial
layer (see ``uber_direct``). This mirrors the payments seam (cash/external in the core,
Stripe Connect commercial).
"""

from __future__ import annotations

from .provider import (
    DeliveryError,
    DeliveryParty,
    DeliveryProvider,
    DeliveryRequest,
    DeliveryResult,
)
from .providers import COMMERCIAL_PROVIDERS, MockCourier, build_provider
from .service import (
    OWN_CHANNELS,
    DeliveryOutcome,
    cancel_delivery,
    delivery_for_order,
    dispatch_delivery,
    get_delivery,
    poll_delivery,
    update_delivery_status,
)
from .states import ACTIVE, CANCELABLE, DELIVERY_STATES, TERMINAL, can_transition

__all__ = [
    "ACTIVE",
    "CANCELABLE",
    "COMMERCIAL_PROVIDERS",
    "DELIVERY_STATES",
    "OWN_CHANNELS",
    "TERMINAL",
    "DeliveryError",
    "DeliveryOutcome",
    "DeliveryParty",
    "DeliveryProvider",
    "DeliveryRequest",
    "DeliveryResult",
    "MockCourier",
    "build_provider",
    "can_transition",
    "cancel_delivery",
    "delivery_for_order",
    "dispatch_delivery",
    "get_delivery",
    "poll_delivery",
    "update_delivery_status",
]
