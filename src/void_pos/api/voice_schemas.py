"""Request/response shapes for the voice-intake endpoints.

The request is the structured order the voice stack built from the call; the response is
the resolved, priced confirmation (the read-back), plus — when placing — the order's number
and state in the pipeline.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..ids import Ulid
from ..voice import (
    DraftItem,
    DraftModifier,
    VoiceOrderConfirmation,
    VoiceOrderDraft,
)


class VoiceModifierIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    modifier_id: Ulid | None = None
    quantity: int = Field(default=1, ge=1, le=999)


class VoiceItemIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    menu_item_id: Ulid | None = None
    quantity: int = Field(default=1, ge=1, le=999)
    modifiers: list[VoiceModifierIn] = Field(default_factory=list, max_length=50)


class VoiceOrderRequest(BaseModel):
    """A confirmed phone order from the voice stack. Items and modifiers may come by name
    (what the caller said) or by menu id (already matched)."""

    items: list[VoiceItemIn] = Field(min_length=1, max_length=100)
    customer_phone: str = Field(min_length=1, max_length=40)
    fulfillment: Literal["pickup", "delivery"] = "pickup"
    customer_name: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=500)
    delivery_address: str | None = Field(default=None, max_length=300)
    order_id: Ulid | None = None  # a caller-stable id makes a retry idempotent

    def to_draft(self) -> VoiceOrderDraft:
        return VoiceOrderDraft(
            items=[
                DraftItem(
                    name=item.name,
                    menu_item_id=item.menu_item_id,
                    quantity=item.quantity,
                    modifiers=[
                        DraftModifier(name=m.name, modifier_id=m.modifier_id, quantity=m.quantity)
                        for m in item.modifiers
                    ],
                )
                for item in self.items
            ],
            customer_phone=self.customer_phone,
            fulfillment=self.fulfillment,
            customer_name=self.customer_name,
            note=self.note,
            delivery_address=self.delivery_address,
            order_id=self.order_id,
        )


class ConfirmedModifierOut(BaseModel):
    name: str
    quantity: int
    price_delta_cents: int


class ConfirmedItemOut(BaseModel):
    name: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int
    modifiers: list[ConfirmedModifierOut]


class VoiceConfirmationOut(BaseModel):
    """The resolved, priced order for the stack to read back before finalising."""

    order_id: str
    customer_phone: str
    customer_name: str | None
    fulfillment: Literal["pickup", "delivery"]
    delivery_address: str | None
    note: str | None
    items: list[ConfirmedItemOut]
    subtotal_cents: int
    total_cents: int
    gst_cents: int
    currency: str
    read_back: str

    @classmethod
    def of(cls, confirmation: VoiceOrderConfirmation) -> VoiceConfirmationOut:
        return cls(
            order_id=confirmation.order_id,
            customer_phone=confirmation.customer_phone,
            customer_name=confirmation.customer_name,
            fulfillment=confirmation.fulfillment,
            delivery_address=confirmation.delivery_address,
            note=confirmation.note,
            items=[
                ConfirmedItemOut(
                    name=item.name,
                    quantity=item.quantity,
                    unit_price_cents=item.unit_price_cents,
                    line_total_cents=item.line_total_cents,
                    modifiers=[
                        ConfirmedModifierOut(
                            name=m.name, quantity=m.quantity, price_delta_cents=m.price_delta_cents
                        )
                        for m in item.modifiers
                    ],
                )
                for item in confirmation.items
            ],
            subtotal_cents=confirmation.subtotal_cents,
            total_cents=confirmation.total_cents,
            gst_cents=confirmation.gst_cents,
            currency=confirmation.currency,
            read_back=confirmation.read_back,
        )


class VoiceOrderResponse(BaseModel):
    """A placed voice order: where it landed in the pipeline, with the confirmation."""

    order_id: str
    order_number: int
    channel: str
    state: str
    replayed: bool
    confirmation: VoiceConfirmationOut
