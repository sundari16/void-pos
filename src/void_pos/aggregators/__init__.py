"""Delivery-aggregator order ingestion: a provider seam plus a built-in mock adapter.

The open core ships the :class:`AggregatorProvider` seam (normalize a partner order, sync
the menu out, accept/reject), an ingestion service that injects an incoming order onto the
existing aggregator channel idempotently, and a :class:`MockAggregator` so the pipeline is
fully testable. The real Uber Eats / DoorDash / Menulog adapters implement the same
protocol against each partner's signed API and live in the commercial layer — the same
open-core-seam-plus-commercial-adapter split the payments module uses for Stripe.
"""

from __future__ import annotations

from .menu import build_menu_snapshot
from .provider import (
    AggregatorError,
    AggregatorPayloadError,
    AggregatorProvider,
    MenuPublishResult,
    MenuSnapshot,
    NormalizedLine,
    NormalizedModifier,
    NormalizedOrder,
    SnapshotGroup,
    SnapshotItem,
    SnapshotModifier,
)
from .providers import COMMERCIAL_PROVIDERS, MockAggregator, build_provider
from .service import (
    AGGREGATOR_DEVICE_NAME,
    AGGREGATOR_ORDER_NUMBER_END,
    AGGREGATOR_ORDER_NUMBER_START,
    PAYMENT_PROVIDER,
    IngestOutcome,
    RejectOutcome,
    get_aggregator_order,
    get_or_create_channel_device,
    ingest_order,
    reject_order,
    sync_menu_out,
)

__all__ = [
    "AGGREGATOR_DEVICE_NAME",
    "AGGREGATOR_ORDER_NUMBER_END",
    "AGGREGATOR_ORDER_NUMBER_START",
    "COMMERCIAL_PROVIDERS",
    "PAYMENT_PROVIDER",
    "AggregatorError",
    "AggregatorPayloadError",
    "AggregatorProvider",
    "IngestOutcome",
    "MenuPublishResult",
    "MenuSnapshot",
    "MockAggregator",
    "NormalizedLine",
    "NormalizedModifier",
    "NormalizedOrder",
    "RejectOutcome",
    "SnapshotGroup",
    "SnapshotItem",
    "SnapshotModifier",
    "build_menu_snapshot",
    "build_provider",
    "get_aggregator_order",
    "get_or_create_channel_device",
    "ingest_order",
    "reject_order",
    "sync_menu_out",
]
