"""On-demand delivery dispatch: send a courier for an own-channel delivery order, track
its status, and cancel it.

A delivery is dispatched idempotently — the dispatching device mints the ``delivery_id``
(a ULID), which is both the row's primary key and the provider's idempotency key, so a
retry after a dropped connection returns the original job and never puts a second courier
on the road. The courier platform is asked *before* the row is written (checks run first),
so a provider that can't be reached leaves nothing recorded and the caller simply retries,
re-using the same idempotency key — the same shape as the Stripe payment path.

Status updates (``accepted`` -> ``picked_up`` -> ``delivered``, or ``canceled`` / ``failed``)
arrive from the courier platform; they move the stored job through the state machine in
:mod:`void_pos.delivery.states`. A repeat of the current status is an idempotent no-op; a
move the machine forbids is refused rather than silently overwriting.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..balance import order_total_cents
from ..clock import utcnow
from ..devices import get_location
from ..errors import Conflict, InvalidRequest, NotFound
from ..models import Delivery, Location
from ..orders import get_order
from . import states
from .provider import DeliveryParty, DeliveryProvider, DeliveryRequest

# Own channels dispatch their own couriers. Aggregator orders (Uber Eats / DoorDash /
# Menulog) arrive already carrying the platform's own delivery — never dispatch for those.
OWN_CHANNELS = frozenset({"pos", "voice", "qr", "web"})


@dataclass(frozen=True)
class DeliveryOutcome:
    delivery: Delivery
    replayed: bool  # True when this returned an existing job rather than acting again


def dispatch_delivery(
    session: Session,
    location_id: str,
    order_id: str,
    provider: DeliveryProvider,
    *,
    delivery_id: str,
    dropoff: DeliveryParty,
    order_value_cents: int | None = None,
) -> DeliveryOutcome:
    """Ask ``provider`` for a courier to take ``order_id`` from the venue to ``dropoff``,
    and record the job. ``delivery_id`` is the till-minted ULID that makes this idempotent.

    Refused (nothing recorded, nothing dispatched) when the order isn't a delivery order on
    an own channel, when it's voided, or when it already has a courier on the way.
    """
    order = get_order(session, order_id, location_id=location_id, for_update=True)

    existing = session.get(Delivery, delivery_id)
    if existing is not None:
        if existing.order_id != order_id:
            raise Conflict(
                "this delivery_id was already used for another order", code="delivery_id_reused"
            )
        return DeliveryOutcome(existing, replayed=True)

    if order.fulfillment != "delivery":
        raise InvalidRequest(
            "only a delivery-fulfillment order can have a courier dispatched",
            code="not_a_delivery_order",
            fulfillment=order.fulfillment,
        )
    if order.channel not in OWN_CHANNELS:
        raise InvalidRequest(
            "aggregator orders carry the platform's own courier; don't dispatch one here",
            code="not_own_channel",
            channel=order.channel,
        )
    if order.state == "voided":
        raise Conflict("this order is voided", code="order_voided")

    active = session.scalar(
        select(Delivery).where(
            Delivery.order_id == order_id, Delivery.status.in_(tuple(states.ACTIVE))
        )
    )
    if active is not None:
        raise Conflict(
            "this order already has a courier on the way", code="delivery_exists",
            delivery_id=active.id,
        )

    location = get_location(session, location_id)
    value_cents = order_total_cents(session, order_id) if order_value_cents is None else order_value_cents
    result = provider.request_delivery(
        DeliveryRequest(
            delivery_id=delivery_id,
            order_reference=str(order.order_number),
            pickup=_venue_party(location),
            dropoff=dropoff,
            order_value_cents=value_cents,
            currency="AUD",
        )
    )

    delivery = Delivery(
        id=delivery_id,
        order_id=order_id,
        location_id=location_id,
        business_date=order.business_date,
        provider=result.provider,
        external_ref=result.external_ref,
        status=result.status,
        fee_cents=result.fee_cents,
        currency=result.currency or "AUD",
        courier_name=result.courier_name,
        tracking_url=result.tracking_url,
        dropoff_name=dropoff.name,
        dropoff_phone=dropoff.phone,
        dropoff_address=dropoff.address,
        dropoff_notes=dropoff.notes,
        requested_at=utcnow(),
    )
    _stamp(delivery, result.status)
    session.add(delivery)
    session.flush()
    return DeliveryOutcome(delivery, replayed=False)


def update_delivery_status(
    session: Session,
    delivery_id: str,
    *,
    status: str,
    location_id: str | None = None,
    external_ref: str | None = None,
    courier_name: str | None = None,
    tracking_url: str | None = None,
    fee_cents: int | None = None,
) -> DeliveryOutcome:
    """Record a status the courier platform reported (its webhook / callback). A repeat of
    the current status is an idempotent no-op; a move the state machine forbids is refused."""
    if status not in states.DELIVERY_STATES:
        raise InvalidRequest(f"unknown delivery status {status!r}", code="unknown_status")
    delivery = get_delivery(session, delivery_id, location_id=location_id)

    if delivery.status == status:
        _apply_metadata(delivery, external_ref, courier_name, tracking_url, fee_cents)
        return DeliveryOutcome(delivery, replayed=True)
    if not states.can_transition(delivery.status, status):
        raise Conflict(
            f"a delivery can't move from {delivery.status} to {status}",
            code="bad_delivery_transition",
            delivery_status=delivery.status,
        )
    delivery.status = status
    _apply_metadata(delivery, external_ref, courier_name, tracking_url, fee_cents)
    _stamp(delivery, status)
    session.flush()
    return DeliveryOutcome(delivery, replayed=False)


def cancel_delivery(
    session: Session,
    delivery_id: str,
    provider: DeliveryProvider,
    *,
    location_id: str | None = None,
    reason: str | None = None,
) -> DeliveryOutcome:
    """Call off a courier the venue no longer needs — only while the courier hasn't
    collected the order yet. The provider is asked to cancel before the row is updated."""
    delivery = get_delivery(session, delivery_id, location_id=location_id)
    if delivery.status == states.CANCELED:
        return DeliveryOutcome(delivery, replayed=True)
    if delivery.status in states.TERMINAL:
        raise Conflict(
            f"this delivery is already {delivery.status}", code="delivery_closed",
            delivery_status=delivery.status,
        )
    if delivery.status not in states.CANCELABLE:
        raise Conflict(
            "the courier already has the order; it's too late to cancel",
            code="too_late_to_cancel",
            delivery_status=delivery.status,
        )
    if delivery.external_ref is not None:
        provider.cancel(delivery.external_ref)
    delivery.status = states.CANCELED
    delivery.canceled_at = utcnow()
    if reason:
        delivery.cancel_reason = reason
    session.flush()
    return DeliveryOutcome(delivery, replayed=False)


def poll_delivery(
    session: Session,
    delivery_id: str,
    provider: DeliveryProvider,
    *,
    location_id: str | None = None,
) -> DeliveryOutcome:
    """Pull the current status from the platform and fold it in. A job with no courier id
    yet, or one already finished, is returned unchanged."""
    delivery = get_delivery(session, delivery_id, location_id=location_id)
    if delivery.external_ref is None or delivery.status in states.TERMINAL:
        return DeliveryOutcome(delivery, replayed=True)
    result = provider.get_status(delivery.external_ref)
    return update_delivery_status(
        session,
        delivery_id,
        status=result.status,
        location_id=location_id,
        courier_name=result.courier_name,
        tracking_url=result.tracking_url,
        fee_cents=result.fee_cents,
    )


def get_delivery(session: Session, delivery_id: str, *, location_id: str | None = None) -> Delivery:
    """The delivery, scoped to a location when given (another venue's job is not found)."""
    delivery = session.get(Delivery, delivery_id)
    if delivery is None or (location_id is not None and delivery.location_id != location_id):
        raise NotFound("delivery not found")
    return delivery


def delivery_for_order(
    session: Session, order_id: str, *, location_id: str | None = None
) -> Delivery:
    """The most recent courier job for an order (there is normally one)."""
    stmt = select(Delivery).where(Delivery.order_id == order_id)
    if location_id is not None:
        stmt = stmt.where(Delivery.location_id == location_id)
    delivery = session.scalars(stmt.order_by(Delivery.created_at.desc())).first()
    if delivery is None:
        raise NotFound("this order has no delivery")
    return delivery


def _venue_party(location: Location) -> DeliveryParty:
    if not location.address:
        raise InvalidRequest(
            "set the venue address first (void-pos venue-details) so a courier can collect",
            code="venue_address_missing",
        )
    return DeliveryParty(
        name=location.business_name or location.name,
        phone=location.phone or "",
        address=location.address,
    )


def _apply_metadata(
    delivery: Delivery,
    external_ref: str | None,
    courier_name: str | None,
    tracking_url: str | None,
    fee_cents: int | None,
) -> None:
    if external_ref is not None and delivery.external_ref is None:
        delivery.external_ref = external_ref
    if courier_name is not None:
        delivery.courier_name = courier_name
    if tracking_url is not None:
        delivery.tracking_url = tracking_url
    if fee_cents is not None:
        delivery.fee_cents = fee_cents


def _stamp(delivery: Delivery, status: str) -> None:
    if status == states.PICKED_UP and delivery.picked_up_at is None:
        delivery.picked_up_at = utcnow()
    elif status == states.DELIVERED and delivery.delivered_at is None:
        delivery.delivered_at = utcnow()
    elif status == states.CANCELED and delivery.canceled_at is None:
        delivery.canceled_at = utcnow()
