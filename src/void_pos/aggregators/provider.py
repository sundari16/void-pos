"""The delivery-aggregator seam: one protocol a partner adapter implements, and the
partner-neutral shapes it speaks in.

A delivery platform (Uber Eats, DoorDash, Menulog) does three things for a venue: it
sends orders in, it takes a published menu out, and it waits to hear whether the venue
will make each order. An ``AggregatorProvider`` is the adapter for one platform:

* ``normalize_order`` turns the platform's order webhook into a :class:`NormalizedOrder`
  the ingestion service can inject, partner-shaped JSON in, our shape out;
* ``build_menu`` / ``publish_menu`` turn a :class:`MenuSnapshot` of the venue's catalog
  into the platform's menu payload and send it;
* ``accept`` / ``reject`` tell the platform the venue's fulfilment decision.

The open core ships this protocol, the neutral shapes, and a :class:`~void_pos.aggregators
.providers.MockAggregator` so the whole pipeline is testable. The real Uber Eats / DoorDash
/ Menulog adapters need signed partner API access and live in the commercial layer — the
same open-core-seam-plus-commercial-adapter split the payments module uses for Stripe.
Nothing here hardcodes a partner API.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..errors import InvalidRequest, ServiceUnavailable


class AggregatorError(ServiceUnavailable):
    """Talking to the delivery partner failed (its API was unreachable or refused)."""

    code = "aggregator_unavailable"


class AggregatorPayloadError(InvalidRequest):
    """A partner order payload couldn't be read into a normalized order."""

    code = "invalid_aggregator_order"


# --- incoming orders ---------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedModifier:
    """One modifier chosen on a line, as a child order line will carry it."""

    name: str
    unit_price_cents: int = 0  # the price delta the partner charged, at sale time
    quantity: int = 1
    kitchen_name: str | None = None
    modifier_id: str | None = None  # our modifier id, when the synced-out menu carried it


@dataclass(frozen=True)
class NormalizedLine:
    """One item the customer ordered, with the price and name the partner charged — a
    snapshot, never read live from our catalog (orders predate or outlive menu edits)."""

    name: str
    unit_price_cents: int
    quantity: int = 1
    kitchen_name: str | None = None
    category: str | None = None
    tax_rate: float = 0.0
    menu_item_id: str | None = None  # our item id, when the synced-out menu carried it
    modifiers: Sequence[NormalizedModifier] = field(default_factory=tuple)


@dataclass(frozen=True)
class NormalizedOrder:
    """A partner order in our terms, ready to inject onto the aggregator channel."""

    external_order_id: str  # the partner's order id: the idempotency key
    lines: Sequence[NormalizedLine]
    fulfillment: str = "delivery"  # pickup | delivery
    display_id: str | None = None  # the short human ref the platform prints, e.g. "UE-7Q2K"
    external_payment_ref: str | None = None  # the partner's payment/charge reference
    currency: str = "AUD"
    customer_name: str | None = None
    notes: str | None = None  # "no onions", courier notes — printed on the kitchen ticket
    placed_at: datetime | None = None  # when the customer placed it at the platform


# --- menu sync out -----------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotModifier:
    id: str
    name: str
    price_delta_cents: int
    kitchen_name: str | None = None


@dataclass(frozen=True)
class SnapshotGroup:
    id: str
    name: str
    min_select: int
    max_select: int | None
    modifiers: Sequence[SnapshotModifier]


@dataclass(frozen=True)
class SnapshotItem:
    id: str
    name: str
    price_cents: int
    category: str | None
    tax_rate: float
    available: bool
    kitchen_name: str | None
    modifier_group_ids: Sequence[str]


@dataclass(frozen=True)
class MenuSnapshot:
    """The venue's live catalog, partner-neutral, as a provider will publish it. Prices
    are integer cents and include GST; availability is at the moment the snapshot is taken."""

    location_id: str
    currency: str
    items: Sequence[SnapshotItem]
    groups: Sequence[SnapshotGroup]


@dataclass(frozen=True)
class MenuPublishResult:
    provider: str
    status: str  # published | queued | skipped
    item_count: int
    external_ref: str | None = None  # the platform's menu/version id, when it returns one


# --- the adapter -------------------------------------------------------------------


class AggregatorProvider(Protocol):
    """A delivery-platform adapter. Built-in: the mock, in the open core. The real Uber
    Eats / DoorDash / Menulog adapters implement this same protocol in the commercial
    layer, where the signed partner API credentials live."""

    name: str  # the platform slug: "mock", "uber_eats", ...

    def normalize_order(self, raw: Mapping[str, Any]) -> NormalizedOrder:
        """The platform's order webhook payload, as a :class:`NormalizedOrder`."""
        ...

    def build_menu(self, snapshot: MenuSnapshot) -> Mapping[str, Any]:
        """The platform's menu payload, built from the venue's catalog snapshot."""
        ...

    def publish_menu(self, payload: Mapping[str, Any]) -> MenuPublishResult:
        """Send the built menu to the platform."""
        ...

    def accept(self, external_order_id: str, *, prep_minutes: int | None = None) -> None:
        """Tell the platform the venue will make this order."""
        ...

    def reject(self, external_order_id: str, *, reason: str) -> None:
        """Tell the platform the venue declines this order (the platform refunds the
        customer)."""
        ...
