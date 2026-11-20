"""The on-demand delivery seam.

A ``DeliveryProvider`` dispatches a courier for one order (pickup at the venue, dropoff at
the customer), reports the job's status, and cancels it. The shapes are partner-neutral:
the open core ships ``MockCourier``; the real Uber Direct adapter (which needs partner
credentials) implements this same protocol in the commercial layer. This mirrors the
payments seam — ``PaymentProvider`` / ``CashProvider`` + the commercial Stripe adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import ServiceUnavailable


class DeliveryError(ServiceUnavailable):
    """The courier platform could not be reached or refused the dispatch. A 503 keeps the
    caller's request (and its idempotency key) intact for a safe retry, exactly like the
    Stripe path: nothing is recorded unless a courier was actually requested."""

    code = "delivery_unavailable"


@dataclass(frozen=True)
class DeliveryParty:
    """One end of a delivery: who the courier meets and where."""

    name: str
    phone: str
    address: str  # a single formatted line; a real adapter geocodes / structures it
    notes: str | None = None
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class DeliveryRequest:
    """Ask a courier platform to carry one order from the venue to the customer.

    ``delivery_id`` is our ULID for the job and doubles as the provider's idempotency key,
    so a retry after a dropped connection requests the same courier once, never twice.
    """

    delivery_id: str
    order_reference: str  # the order number, for the courier's own records / the bag label
    pickup: DeliveryParty
    dropoff: DeliveryParty
    order_value_cents: int = 0  # declared value, for the platform's insurance
    currency: str = "AUD"


@dataclass(frozen=True)
class DeliveryResult:
    """What a provider reports about a job — the same shape for request, poll and cancel."""

    provider: str
    status: str  # one of delivery.states
    external_ref: str | None = None  # the platform's delivery id
    fee_cents: int | None = None  # what the courier charges, in cents
    currency: str = "AUD"
    courier_name: str | None = None
    tracking_url: str | None = None


class DeliveryProvider(Protocol):
    """A courier backend. ``MockCourier`` is the open-core implementation; the Uber Direct
    adapter implements this same protocol in the commercial layer."""

    name: str

    def request_delivery(self, request: DeliveryRequest) -> DeliveryResult: ...

    def get_status(self, external_ref: str) -> DeliveryResult: ...

    def cancel(self, external_ref: str) -> DeliveryResult: ...
