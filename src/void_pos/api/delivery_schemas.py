from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from ..clock import as_aware
from ..ids import Ulid
from ..models import Delivery

# The courier-reported statuses a callback may carry (see void_pos.delivery.states).
DeliveryStatus = Literal["requested", "accepted", "picked_up", "delivered", "canceled", "failed"]

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class DeliveryDispatchRequest(BaseModel):
    """Send a courier for an own-channel delivery order. ``delivery_id`` is the ULID the
    till mints; a retry with the same id returns the same job (no second courier)."""

    delivery_id: Ulid
    dropoff_name: Annotated[Text, StringConstraints(max_length=120)]
    dropoff_phone: Annotated[Text, StringConstraints(max_length=40)]
    dropoff_address: Annotated[Text, StringConstraints(max_length=300)]
    dropoff_notes: str | None = Field(default=None, max_length=300)
    # Declared order value for the platform's insurance; defaults to the order total.
    order_value_cents: int | None = Field(default=None, ge=0)


class DeliveryStatusRequest(BaseModel):
    """A status the courier platform reported (its webhook / callback)."""

    status: DeliveryStatus
    courier_name: str | None = Field(default=None, max_length=120)
    tracking_url: str | None = Field(default=None, max_length=500)
    fee_cents: int | None = Field(default=None, ge=0)
    external_ref: str | None = Field(default=None, max_length=200)


class DeliveryCancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class DeliveryResponse(BaseModel):
    delivery_id: str
    order_id: str
    location_id: str
    provider: str
    external_ref: str | None
    status: str
    fee_cents: int | None
    currency: str
    courier_name: str | None
    tracking_url: str | None
    dropoff_name: str
    dropoff_phone: str
    dropoff_address: str
    dropoff_notes: str | None
    cancel_reason: str | None
    requested_at: datetime | None
    picked_up_at: datetime | None
    delivered_at: datetime | None
    canceled_at: datetime | None
    replayed: bool = False

    @classmethod
    def of(cls, delivery: Delivery, *, replayed: bool = False) -> DeliveryResponse:
        def at(value: datetime | None) -> datetime | None:
            return as_aware(value) if value is not None else None

        return cls(
            delivery_id=delivery.id,
            order_id=delivery.order_id,
            location_id=delivery.location_id,
            provider=delivery.provider,
            external_ref=delivery.external_ref,
            status=delivery.status,
            fee_cents=delivery.fee_cents,
            currency=delivery.currency,
            courier_name=delivery.courier_name,
            tracking_url=delivery.tracking_url,
            dropoff_name=delivery.dropoff_name,
            dropoff_phone=delivery.dropoff_phone,
            dropoff_address=delivery.dropoff_address,
            dropoff_notes=delivery.dropoff_notes,
            cancel_reason=delivery.cancel_reason,
            requested_at=at(delivery.requested_at),
            picked_up_at=at(delivery.picked_up_at),
            delivered_at=at(delivery.delivered_at),
            canceled_at=at(delivery.canceled_at),
            replayed=replayed,
        )
