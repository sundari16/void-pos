"""Payload schemas for each event type the order pipeline understands.

Every event is validated against its schema before it reaches the log, whether it came
from an online command or a device's offline sync batch. Unknown keys are tolerated so a
newer device can send extra fields to an older server. Money and counts are strict
(``"1001"`` is not an order number), so the stored payload is exactly what passed
validation and the projection can read it as-is.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..ids import Ulid
from .types import EventType

MAX_LINE_CENTS = 10_000_000  # $100,000 per line: a typo guard, not a business limit

Channel = Literal["pos", "voice", "qr", "web", "aggregator"]
Fulfillment = Literal["pickup", "delivery", "dine_in"]
CURRENCY_PATTERN = r"^[A-Z]{3}$"


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class OrderLineIn(_Payload):
    """One order line. A modifier is a child line pointing at its item line."""

    id: Ulid
    parent_line_id: Ulid | None = None
    menu_item_id: Ulid | None = None
    modifier_id: Ulid | None = None
    quantity: int = Field(default=1, ge=1, le=999, strict=True)
    name_snapshot: str = Field(min_length=1, max_length=200)
    kitchen_name_snapshot: str | None = Field(default=None, max_length=200)
    unit_price_cents: int = Field(ge=-MAX_LINE_CENTS, le=MAX_LINE_CENTS, strict=True)
    tax_rate_snapshot: float = Field(default=0, ge=0, le=1, strict=True)
    category_snapshot: str | None = Field(default=None, max_length=120)


def check_line_tree(lines: list[OrderLineIn]) -> None:
    """Modifiers sit one level under an item in the same order; prices add up to >= 0."""
    ids = [line.id for line in lines]
    if len(set(ids)) != len(ids):
        raise ValueError("line ids must be unique within an order")
    item_ids = {line.id for line in lines if line.parent_line_id is None}
    if not item_ids:
        raise ValueError("an order needs at least one item line")
    for line in lines:
        if line.parent_line_id is None:
            if line.unit_price_cents < 0:
                raise ValueError(f"item line {line.id} can't have a negative price")
        elif line.parent_line_id not in item_ids:
            raise ValueError(
                f"modifier line {line.id} must point at an item line in the same order"
            )
    if sum(line.unit_price_cents * line.quantity for line in lines) < 0:
        raise ValueError("the order total can't be negative")


class OrderPlaced(_Payload):
    order_id: Ulid
    order_number: int = Field(ge=1, strict=True)
    channel: Channel
    fulfillment: Fulfillment
    customer_id: Ulid | None = None
    lines: list[OrderLineIn] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _line_tree(self) -> OrderPlaced:
        check_line_tree(self.lines)
        return self


class OrderRef(_Payload):
    order_id: Ulid


class PaymentIn(_Payload):
    id: Ulid
    provider: str = Field(min_length=1, max_length=40)
    external_ref: str | None = Field(default=None, max_length=200)
    amount_cents: int = Field(ge=0, le=100 * MAX_LINE_CENTS, strict=True)
    currency: str = Field(default="AUD", pattern=CURRENCY_PATTERN)
    status: Literal["captured", "pending", "failed"] = "captured"


class OrderPaid(_Payload):
    order_id: Ulid
    payment: PaymentIn | None = None


class LoyaltyRecorded(_Payload):
    id: Ulid
    customer_id: Ulid
    order_id: Ulid | None = None
    points_delta: int = Field(strict=True)
    reason: str = Field(min_length=1, max_length=80)


PAYLOAD_SCHEMAS: dict[str, type[BaseModel]] = {
    EventType.ORDER_PLACED: OrderPlaced,
    EventType.ORDER_SENT: OrderRef,
    EventType.ORDER_VOIDED: OrderRef,
    EventType.ORDER_PAID: OrderPaid,
    EventType.LOYALTY_RECORDED: LoyaltyRecorded,
}
