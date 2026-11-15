from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LineIn(BaseModel):
    """One requested line. `ref` is a client-local id unique within the request;
    a modifier points at its parent item line via `parent_ref`."""

    ref: str
    parent_ref: str | None = None
    menu_item_id: str | None = None
    modifier_id: str | None = None
    quantity: int = 1
    name_snapshot: str
    kitchen_name_snapshot: str | None = None
    unit_price_cents: int
    tax_rate_snapshot: float = 0
    category_snapshot: str | None = None


class PlaceOrderRequest(BaseModel):
    device_id: str
    location_id: str
    order_number: int
    channel: str
    fulfillment: str
    customer_id: str | None = None
    business_date: date | None = None
    lines: list[LineIn]


class OrderResponse(BaseModel):
    id: str
    order_number: int
    state: str


class KitchenResponse(BaseModel):
    order_id: str
    state: str
    ticket_bytes: int


class PayRequest(BaseModel):
    method: str = "cash"  # cash | external (Stripe plugs in as a commercial method)
    external_ref: str | None = None
    amount_cents: int | None = None  # defaults to the order total
    currency: str = "AUD"


class PaymentResponse(BaseModel):
    order_id: str
    payment_id: str
    provider: str
    amount_cents: int
    status: str
    order_state: str


class EventIn(BaseModel):
    event_id: str
    device_id: str
    seq: int
    location_id: str
    business_date: date
    occurred_at: datetime
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class PushRequest(BaseModel):
    events: list[EventIn]


class Ack(BaseModel):
    event_id: str
    status: str


class PushResponse(BaseModel):
    acks: list[Ack]
    cursor: str | None = None


class PullResponse(BaseModel):
    events: list[dict[str, Any]]
    next_cursor: str | None = None
    has_more: bool


class MenuItemIn(BaseModel):
    name: str
    base_price_cents: int
    kitchen_name: str | None = None
    category: str | None = None
    tax_rate: float = 0
    is_available: bool = True


class MenuItemUpdate(BaseModel):
    name: str | None = None
    base_price_cents: int | None = None
    kitchen_name: str | None = None
    category: str | None = None
    tax_rate: float | None = None
    is_available: bool | None = None


class MenuItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    kitchen_name: str | None
    category: str | None
    base_price_cents: int
    tax_rate: float
    is_available: bool


class ModifierGroupIn(BaseModel):
    name: str
    min_select: int = 0
    max_select: int | None = None


class ModifierGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    location_id: str
    name: str
    min_select: int
    max_select: int | None


class ModifierIn(BaseModel):
    name: str
    price_delta_cents: int = 0
    kitchen_name: str | None = None


class ModifierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    group_id: str
    name: str
    kitchen_name: str | None
    price_delta_cents: int
