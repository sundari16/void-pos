"""Request/response shapes for the public storefront (``/online/*``).

Kept apart from the till schemas: these are what a customer's browser sends and sees, with
no bearer token and no till concepts (seq, device, manager PIN).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from ..events.payloads import MAX_LINE_CENTS, OrderLineIn, check_line_tree
from ..ids import Ulid
from ..loyalty import LoyaltyRules
from ..online import WebOrderResult
from ..orders.views import OrderView
from .schemas import OrderResponse, OrderSummary, TillMenuOut

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class LoyaltyInfo(BaseModel):
    enabled: bool
    earn_points_per_dollar: int
    redeem_cents_per_point: int

    @classmethod
    def of(cls, rules: LoyaltyRules) -> LoyaltyInfo:
        return cls(
            enabled=rules.enabled,
            earn_points_per_dollar=rules.earn_points_per_dollar,
            redeem_cents_per_point=rules.redeem_cents_per_point,
        )


class StorefrontOut(BaseModel):
    """Everything the storefront needs to render a location: its menu plus what it can do
    (pay online, loyalty)."""

    location_id: str
    location_name: str
    timezone: str
    currency: str = "AUD"
    online_pay_enabled: bool  # Stripe is set up on the server
    loyalty: LoyaltyInfo
    menu: TillMenuOut


class OnlineCustomerIn(BaseModel):
    name: Name
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    marketing_opt_in: bool = False

    @model_validator(mode="after")
    def _contactable(self) -> OnlineCustomerIn:
        if not (self.phone and self.phone.strip()) and not (self.email and self.email.strip()):
            raise ValueError("give a phone number or an email address")
        return self


class PlaceWebOrderRequest(BaseModel):
    """Place a customer order on the web channel."""

    order_id: Ulid  # the browser mints it; a resend replays rather than placing twice
    customer: OnlineCustomerIn
    fulfillment: Literal["pickup", "delivery"] = "pickup"
    delivery_address: str | None = Field(default=None, max_length=400)
    customer_note: str | None = Field(default=None, max_length=400)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=500)
    redeem_points: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _lines_ok(self) -> PlaceWebOrderRequest:
        check_line_tree(self.lines)
        return self


class OnlineOrderOut(OrderResponse):
    """The order as the customer sees it, with the online-only fields."""

    customer_id: str | None
    delivery_address: str | None
    customer_note: str | None

    @classmethod
    def of_web(cls, view: OrderView, *, replayed: bool = False) -> OnlineOrderOut:
        base = OrderResponse.of_view(view, replayed=replayed).model_dump()
        o = view.order
        return cls(
            **base,
            customer_id=o.customer_id,
            delivery_address=o.delivery_address,
            customer_note=o.customer_note,
        )


class WebOrderOut(BaseModel):
    order: OnlineOrderOut
    customer_id: str
    loyalty_balance: int
    points_earned: int
    redeemed_points: int
    redeemed_cents: int

    @classmethod
    def of(cls, result: WebOrderResult, view: OrderView) -> WebOrderOut:
        return cls(
            order=OnlineOrderOut.of_web(view, replayed=result.replayed),
            customer_id=result.customer.id,
            loyalty_balance=result.loyalty_balance,
            points_earned=result.points_earned,
            redeemed_points=result.redeemed_points,
            redeemed_cents=result.redeemed_cents,
        )


class WebStripeIntentRequest(BaseModel):
    payment_id: Ulid
    tip_cents: int = Field(default=0, ge=0, le=MAX_LINE_CENTS)


class WebStripeIntentOut(BaseModel):
    payment_intent_id: str
    client_secret: str
    publishable_key: str


class WebPayRequest(BaseModel):
    payment_id: Ulid
    stripe_payment_intent_id: str = Field(min_length=1, max_length=100)
    tip_cents: int = Field(default=0, ge=0, le=MAX_LINE_CENTS)


class WebPaymentOut(BaseModel):
    order_id: str
    payment_id: str
    provider: str
    amount_cents: int
    tip_cents: int
    currency: str
    status: str
    order_state: str
    balance_cents: int
    replayed: bool


class LoyaltyBalanceOut(BaseModel):
    customer_id: str
    enabled: bool
    balance: int
    earn_points_per_dollar: int
    redeem_cents_per_point: int


class CustomerOrdersOut(BaseModel):
    customer_id: str
    orders: list[OrderSummary]
