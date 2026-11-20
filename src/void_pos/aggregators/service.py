"""Ingest delivery-aggregator orders into the same order pipeline as the till.

An incoming partner order becomes an aggregator-channel order through the ordinary event
log — ``order.placed``, ``order.paid``, ``order.sent`` — so it lists, prints, reports and
reconciles like any other order. Three things keep that safe and faithful to the data
model:

* **Idempotent by the partner's order id.** One :class:`~void_pos.models.AggregatorOrder`
  row per ``(provider, external_order_id)`` is the dedupe key: the same webhook twice
  returns the order already made, never a second one.
* **The payment is the partner's.** The platform charged the customer, so the order is
  recorded paid with provider ``"aggregator"`` and the partner's external reference — never
  keyed on the partner's id, which lives beside ours on the tie row.
* **A channel device, not a till.** Every order is attributed to a device (its
  order-number range, its ``seq``); aggregator orders go through one virtual "Aggregators"
  device per location, in a reserved high number band, so they never collide with a till.

``reject_order`` is the other half of the partner handshake: declining before anything
enters the sales pipeline (the platform refunds the customer). ``sync_menu_out`` publishes
the catalog to a platform. The real partner adapters are commercial; see ``providers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..balance import balance_cents
from ..clock import local_date, utcnow
from ..commands import Command, record
from ..devices import get_location, next_order_number
from ..errors import Conflict, InvalidRequest
from ..events import EventType
from ..ids import new_ulid
from ..models import ACCEPTED, REJECTED, AggregatorOrder, Device, Order
from ..printing import Printer, Rasterizer, print_order
from .menu import build_menu_snapshot
from .provider import AggregatorProvider, MenuPublishResult, NormalizedOrder

# The money is the platform's: a till never processes it, so its own provider name.
PAYMENT_PROVIDER = "aggregator"

# The virtual device aggregator orders are attributed to, one per location. Its reserved
# order-number band sits far above any till's range so kitchen call-outs never collide.
AGGREGATOR_DEVICE_NAME = "Aggregators"
AGGREGATOR_ORDER_NUMBER_START = 900_001
AGGREGATOR_ORDER_NUMBER_END = 999_999


def get_or_create_channel_device(
    session: Session, location_id: str, *, for_update: bool = False
) -> Device:
    """The location's virtual aggregator device, created on first use. Unlike a till it is
    never paired and has no token — it exists only to attribute aggregator-channel events.
    ``for_update`` locks the row so concurrent ingests serialise seq/order-number
    allocation (a no-op on SQLite, where tests have no real concurrency)."""
    get_location(session, location_id)  # 404 if the location is unknown
    device = session.scalars(
        select(Device).where(
            Device.location_id == location_id,
            Device.name == AGGREGATOR_DEVICE_NAME,
            Device.order_number_start == AGGREGATOR_ORDER_NUMBER_START,
        )
    ).first()
    if device is None:
        device = Device(
            location_id=location_id,
            name=AGGREGATOR_DEVICE_NAME,
            order_number_start=AGGREGATOR_ORDER_NUMBER_START,
            order_number_end=AGGREGATOR_ORDER_NUMBER_END,
        )
        session.add(device)
        session.flush()
    if for_update:
        session.get(Device, device.id, with_for_update=True)
    return device


def get_aggregator_order(
    session: Session, provider: str, external_order_id: str
) -> AggregatorOrder | None:
    """The tie row for a partner order, if we've seen it before (the dedupe key)."""
    return session.scalars(
        select(AggregatorOrder).where(
            AggregatorOrder.provider == provider,
            AggregatorOrder.external_order_id == external_order_id,
        )
    ).first()


@dataclass(frozen=True)
class IngestOutcome:
    order: Order
    tie: AggregatorOrder
    replayed: bool  # the partner order had already been ingested


@dataclass(frozen=True)
class RejectOutcome:
    tie: AggregatorOrder
    replayed: bool


def ingest_order(
    session: Session,
    provider: AggregatorProvider,
    location_id: str,
    raw: dict[str, Any],
    *,
    prep_minutes: int | None = None,
    printer: Printer | None = None,
    rasterizer: Rasterizer | None = None,
    business_date: date | None = None,
) -> IngestOutcome:
    """Inject a partner order as a paid, kitchen-bound aggregator-channel order, and confirm
    it with the partner. Idempotent on the partner's order id: ingesting the same order
    again returns the one already made. An order previously rejected can't be ingested."""
    normalized = provider.normalize_order(raw)
    existing = get_aggregator_order(session, provider.name, normalized.external_order_id)
    if existing is not None:
        if existing.status == REJECTED:
            raise Conflict(
                "this order was rejected; it can't be ingested",
                code="aggregator_order_rejected",
            )
        order = session.get(Order, existing.order_id)
        assert order is not None, "an accepted tie always points at its order"
        return IngestOutcome(order, existing, replayed=True)

    location = get_location(session, location_id)
    device = get_or_create_channel_device(session, location_id, for_update=True)
    day = business_date or local_date(location.timezone)
    order_id = new_ulid()
    order_number = next_order_number(session, device)

    _record(session, device, EventType.ORDER_PLACED, _placed_payload(normalized, order_id, order_number), day)
    # The platform already charged the customer: record it paid, provider "aggregator".
    _record(
        session,
        device,
        EventType.ORDER_PAID,
        {
            "order_id": order_id,
            "payment": {
                "id": new_ulid(),
                "provider": PAYMENT_PROVIDER,
                "external_ref": normalized.external_payment_ref or normalized.external_order_id,
                "amount_cents": balance_cents(session, order_id),
                "currency": normalized.currency.upper(),
                "status": "captured",
            },
        },
        day,
    )
    # Fire it to the kitchen, like any online order (print best-effort if a printer is set).
    if printer is not None:
        print_order(session, order_id, printer, rasterizer, heading="ONLINE")
    _record(session, device, EventType.ORDER_SENT, {"order_id": order_id}, day)

    provider.accept(normalized.external_order_id, prep_minutes=prep_minutes)
    tie = AggregatorOrder(
        location_id=location_id,
        order_id=order_id,
        provider=provider.name,
        external_order_id=normalized.external_order_id,
        display_id=normalized.display_id,
        external_payment_ref=normalized.external_payment_ref,
        status=ACCEPTED,
        currency=normalized.currency.upper(),
        raw=dict(raw),
        decided_at=utcnow(),
    )
    session.add(tie)
    session.flush()
    order = session.get(Order, order_id)
    assert order is not None, "order.placed projects its order"
    return IngestOutcome(order, tie, replayed=False)


def reject_order(
    session: Session,
    provider: AggregatorProvider,
    location_id: str,
    raw: dict[str, Any],
    *,
    reason: str,
) -> RejectOutcome:
    """Decline a partner order before it enters the sales pipeline (the platform refunds
    the customer). No order is created. Idempotent on the partner's order id; an order
    already accepted can't be rejected this way (void or refund it through the order)."""
    get_location(session, location_id)
    normalized = provider.normalize_order(raw)
    existing = get_aggregator_order(session, provider.name, normalized.external_order_id)
    if existing is not None:
        if existing.status == ACCEPTED:
            raise Conflict(
                "this order was already accepted; void or refund it through the order",
                code="aggregator_order_accepted",
            )
        return RejectOutcome(existing, replayed=True)

    provider.reject(normalized.external_order_id, reason=reason)
    tie = AggregatorOrder(
        location_id=location_id,
        order_id=None,
        provider=provider.name,
        external_order_id=normalized.external_order_id,
        display_id=normalized.display_id,
        external_payment_ref=normalized.external_payment_ref,
        status=REJECTED,
        reason=reason[:200],
        currency=normalized.currency.upper(),
        raw=dict(raw),
        decided_at=utcnow(),
    )
    session.add(tie)
    session.flush()
    return RejectOutcome(tie, replayed=False)


def sync_menu_out(
    session: Session, provider: AggregatorProvider, location_id: str
) -> MenuPublishResult:
    """Build the venue's live menu as a partner payload and publish it to the platform."""
    snapshot = build_menu_snapshot(session, location_id)
    payload = provider.build_menu(snapshot)
    return provider.publish_menu(payload)


# --- internals ---------------------------------------------------------------------


def _record(
    session: Session, device: Device, event_type: str, payload: dict[str, Any], day: date
) -> None:
    """Append one aggregator-channel event on the channel device, allocating the next seq.
    After each append the device's ``last_seq`` is advanced, so the next call reads it fresh."""
    command = Command(event_id=new_ulid(), seq=device.last_seq + 1)
    record(session, device, command, event_type=event_type, payload=payload, business_date=day)


def _placed_payload(normalized: NormalizedOrder, order_id: str, order_number: int) -> dict[str, Any]:
    """An ``order.placed`` payload from a normalized order. Each item becomes an item line
    with its modifiers as child lines; names, prices, tax and category are snapshots taken
    from what the partner charged, never read live from the catalog."""
    lines: list[dict[str, Any]] = []
    for item in normalized.lines:
        item_line_id = new_ulid()
        lines.append(
            {
                "id": item_line_id,
                "menu_item_id": item.menu_item_id,
                "quantity": item.quantity,
                "name_snapshot": item.name,
                "kitchen_name_snapshot": item.kitchen_name,
                "unit_price_cents": item.unit_price_cents,
                "tax_rate_snapshot": item.tax_rate,
                "category_snapshot": item.category,
            }
        )
        for mod in item.modifiers:
            lines.append(
                {
                    "id": new_ulid(),
                    "parent_line_id": item_line_id,
                    "modifier_id": mod.modifier_id,
                    "quantity": mod.quantity,
                    "name_snapshot": mod.name,
                    "kitchen_name_snapshot": mod.kitchen_name,
                    "unit_price_cents": mod.unit_price_cents,
                    "tax_rate_snapshot": item.tax_rate,  # the add-on is taxed like its item
                }
            )
    if normalized.fulfillment not in ("pickup", "delivery", "dine_in"):
        raise InvalidRequest(
            f"unknown fulfillment {normalized.fulfillment!r}; expected pickup or delivery",
            code="invalid_fulfillment",
        )
    return {
        "order_id": order_id,
        "order_number": order_number,
        "channel": "aggregator",
        "fulfillment": normalized.fulfillment,
        # Give the kitchen and receipt the platform's short ref to match the order to the bag.
        "table_label": normalized.display_id[:40] if normalized.display_id else None,
        "lines": lines,
    }
