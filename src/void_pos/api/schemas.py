from __future__ import annotations

from datetime import date

from pydantic import BaseModel


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
