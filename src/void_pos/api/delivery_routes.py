"""On-demand delivery dispatch: send a courier for an own-channel delivery order, track
its status, and cancel it. The open core drives the ``mock`` courier; the Uber Direct
adapter is commercial.

Who may call what: a paired till dispatches and cancels for its own location; anyone at the
location (or the admin) can track; the status callback takes the admin token, standing in
for a courier platform's signed webhook (a real adapter verifies the platform's signature).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..delivery import (
    DeliveryParty,
    DeliveryProvider,
    build_provider,
    cancel_delivery,
    delivery_for_order,
    dispatch_delivery,
    get_delivery,
    update_delivery_status,
)
from ..errors import ServiceUnavailable
from .auth import AdminDep, DeviceDep, PrincipalDep
from .delivery_schemas import (
    DeliveryCancelRequest,
    DeliveryDispatchRequest,
    DeliveryResponse,
    DeliveryStatusRequest,
)
from .deps import get_session

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _provider(settings: Settings) -> DeliveryProvider:
    """The configured courier, or a 503 when delivery is switched off for this server."""
    if not settings.delivery_enabled:
        raise ServiceUnavailable(
            "on-demand delivery dispatch is off on this server", code="delivery_disabled"
        )
    return build_provider(settings.delivery_provider)


@router.post("/orders/{order_id}/delivery", response_model=DeliveryResponse, tags=["delivery"])
def dispatch_route(
    order_id: str,
    body: DeliveryDispatchRequest,
    session: SessionDep,
    device: DeviceDep,
    settings: SettingsDep,
) -> DeliveryResponse:
    """Dispatch a courier for this delivery order (pickup = venue, dropoff = the customer).
    Idempotent on ``delivery_id``: a retry returns the same job."""
    outcome = dispatch_delivery(
        session,
        device.location_id,
        order_id,
        _provider(settings),
        delivery_id=body.delivery_id,
        dropoff=DeliveryParty(
            name=body.dropoff_name,
            phone=body.dropoff_phone,
            address=body.dropoff_address,
            notes=body.dropoff_notes,
        ),
        order_value_cents=body.order_value_cents,
    )
    session.commit()
    return DeliveryResponse.of(outcome.delivery, replayed=outcome.replayed)


@router.get("/orders/{order_id}/delivery", response_model=DeliveryResponse, tags=["delivery"])
def track_by_order(
    order_id: str, session: SessionDep, principal: PrincipalDep
) -> DeliveryResponse:
    """The courier job for an order."""
    delivery = delivery_for_order(session, order_id, location_id=principal.location_id)
    return DeliveryResponse.of(delivery)


@router.get("/deliveries/{delivery_id}", response_model=DeliveryResponse, tags=["delivery"])
def track_delivery(
    delivery_id: str, session: SessionDep, principal: PrincipalDep
) -> DeliveryResponse:
    delivery = get_delivery(session, delivery_id, location_id=principal.location_id)
    return DeliveryResponse.of(delivery)


@router.post(
    "/deliveries/{delivery_id}/status", response_model=DeliveryResponse, tags=["delivery"]
)
def status_callback(
    delivery_id: str, body: DeliveryStatusRequest, session: SessionDep, _: AdminDep
) -> DeliveryResponse:
    """Record a status the courier platform reported. In the open core this takes the admin
    token and the CLI / a test drives it; a real adapter calls it from a verified webhook."""
    outcome = update_delivery_status(
        session,
        delivery_id,
        status=body.status,
        external_ref=body.external_ref,
        courier_name=body.courier_name,
        tracking_url=body.tracking_url,
        fee_cents=body.fee_cents,
    )
    session.commit()
    return DeliveryResponse.of(outcome.delivery, replayed=outcome.replayed)


@router.post(
    "/deliveries/{delivery_id}/cancel", response_model=DeliveryResponse, tags=["delivery"]
)
def cancel_route(
    delivery_id: str,
    body: DeliveryCancelRequest,
    session: SessionDep,
    device: DeviceDep,
    settings: SettingsDep,
) -> DeliveryResponse:
    """Call off a courier the venue no longer needs (only before the courier collects)."""
    outcome = cancel_delivery(
        session,
        delivery_id,
        _provider(settings),
        location_id=device.location_id,
        reason=body.reason,
    )
    session.commit()
    return DeliveryResponse.of(outcome.delivery, replayed=outcome.replayed)
