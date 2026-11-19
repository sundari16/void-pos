"""The storefront write path: place and pay a customer ``web`` order.

An online order flows through the very same pipeline a till uses — ``place_order``,
``pay_order``, the loyalty ledger — driven by the location's web device rather than a
paired till. All of it is idempotent on the customer-minted ``order_id``: a double-submit
returns the order already placed, never a second one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..balance import balance_cents, order_total_cents
from ..commands import Command
from ..config import Settings
from ..crm import upsert_customer
from ..devices import get_location, next_order_number
from ..errors import Conflict, InvalidRequest, NotFound, VoidPosError
from ..events import OrderPlaced
from ..events.payloads import OrderLineIn
from ..events.types import UNSETTLED
from ..ids import new_ulid
from ..loyalty import (
    balance as loyalty_balance,
)
from ..loyalty import (
    max_redeemable_points,
    points_earned,
    record_entry,
    redemption_value_cents,
    rules_from_settings,
)
from ..models import Customer, Device, LoyaltyLedger, Order, Payment
from ..orders import get_order, place_order
from ..payments import LoyaltyProvider, pay_order
from ..payments.service import PaymentOutcome
from ..payments.stripe import RELEASE_ON, StripeApi, StripeProvider, release
from .device import web_device


@dataclass(frozen=True)
class WebOrderResult:
    order: Order
    customer: Customer
    loyalty_balance: int
    points_earned: int
    redeemed_points: int
    redeemed_cents: int
    replayed: bool


def _require_online(settings: Settings) -> None:
    if not settings.online_ordering_enabled:
        raise NotFound("online ordering isn't available here")


def _command(device: Device) -> Command:
    """A fresh command for the web device: a new event id and its next sequence. The device
    row is locked by the caller, and each recorded event advances ``last_seq``."""
    return Command(event_id=new_ulid(), seq=device.last_seq + 1)


def place_web_order(
    session: Session,
    settings: Settings,
    *,
    location_id: str,
    order_id: str,
    name: str | None,
    phone: str | None,
    email: str | None,
    marketing_opt_in: bool,
    fulfillment: Literal["pickup", "delivery"],
    delivery_address: str | None,
    customer_note: str | None,
    lines: list[OrderLineIn],
    redeem_points: int = 0,
) -> WebOrderResult:
    """Place a ``web`` order into the pipeline, capture the customer, and apply loyalty.

    The order is checked against the live menu (``place_order``'s rules), so an item that
    isn't on sale is refused just as it would be on a till. Points are redeemed as a
    non-cash tender toward the bill and earned on what the customer actually pays.
    """
    _require_online(settings)
    location = get_location(session, location_id)
    if fulfillment == "delivery" and not (delivery_address and delivery_address.strip()):
        raise InvalidRequest("a delivery order needs an address", code="delivery_address_required")

    existing = session.get(Order, order_id)
    if existing is not None:
        if existing.channel != "web":
            raise Conflict("that order id is already used", code="order_exists")
        return _result(session, settings, existing, replayed=True)

    customer = upsert_customer(
        session,
        org_id=location.org_id,
        location_id=location_id,
        name=name,
        phone=phone,
        email=email,
        marketing_opt_in=marketing_opt_in,
    )
    device = web_device(session, location_id, for_update=True)
    placed = OrderPlaced(
        order_id=order_id,
        order_number=next_order_number(session, device),
        channel="web",
        fulfillment=fulfillment,
        customer_id=customer.id,
        delivery_address=(delivery_address.strip() if delivery_address else None),
        customer_note=(customer_note.strip() if customer_note else None),
        lines=lines,
    )
    place_order(session, device, _command(device), placed)

    rules = rules_from_settings(settings)
    total = order_total_cents(session, order_id)
    if rules.enabled and redeem_points > 0:
        point_balance = loyalty_balance(session, customer.id)
        redeemed_points = min(redeem_points, max_redeemable_points(point_balance, total, rules))
        redeemed_cents = redemption_value_cents(redeemed_points, rules)
        if redeemed_points > 0:
            ledger_id = record_entry(
                session,
                device,
                _command(device),
                customer_id=customer.id,
                points_delta=-redeemed_points,
                reason="redeem",
                order_id=order_id,
            )
            pay_order(
                session,
                device,
                order_id,
                LoyaltyProvider(ledger_id),
                _command(device),
                payment_id=new_ulid(),
                amount_cents=redeemed_cents,
            )

    if rules.enabled:
        spend = max(order_total_cents(session, order_id) - _redeemed_cents(session, order_id), 0)
        earned = points_earned(spend, rules)
        if earned > 0:
            record_entry(
                session,
                device,
                _command(device),
                customer_id=customer.id,
                points_delta=earned,
                reason="earn",
                order_id=order_id,
            )

    return _result(session, settings, get_order(session, order_id), replayed=False)


def _redeemed_cents(session: Session, order_id: str) -> int:
    total = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id, Payment.provider == "loyalty"
        )
    )
    return int(total or 0)


def _order_loyalty(session: Session, order_id: str) -> tuple[int, int]:
    """(points earned, points redeemed) recorded against this order on the ledger."""
    earned = session.scalar(
        select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
            LoyaltyLedger.order_id == order_id, LoyaltyLedger.points_delta > 0
        )
    )
    redeemed = session.scalar(
        select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
            LoyaltyLedger.order_id == order_id, LoyaltyLedger.points_delta < 0
        )
    )
    return int(earned or 0), -int(redeemed or 0)


def _result(
    session: Session, settings: Settings, order: Order, *, replayed: bool
) -> WebOrderResult:
    customer = session.get(Customer, order.customer_id) if order.customer_id else None
    earned, redeemed_points = _order_loyalty(session, order.id)
    bal = loyalty_balance(session, customer.id) if customer is not None else 0
    assert customer is not None, "a web order always has a customer"
    return WebOrderResult(
        order=order,
        customer=customer,
        loyalty_balance=bal,
        points_earned=earned,
        redeemed_points=redeemed_points,
        redeemed_cents=_redeemed_cents(session, order.id),
        replayed=replayed,
    )


def get_web_order(session: Session, order_id: str) -> Order:
    """A web order by id (another channel's order is not found here)."""
    order = session.get(Order, order_id)
    if order is None or order.channel != "web":
        raise NotFound("order not found")
    return order


def web_stripe_intent(
    session: Session,
    stripe: StripeApi,
    *,
    order_id: str,
    payment_id: str,
    tip_cents: int = 0,
) -> dict[str, str]:
    """Start a card payment for the whole remaining balance of a web order."""
    order = get_web_order(session, order_id)
    if order.state not in UNSETTLED:
        raise Conflict(f"this order is already {order.state}", code="order_closed")
    owing = balance_cents(session, order_id)
    if owing <= 0:
        raise Conflict("there's nothing left to pay on this order", code="nothing_owing")
    intent = stripe.create_intent(
        amount_cents=owing + tip_cents,
        currency="aud",
        idempotency_key=f"intent-{payment_id}",
        metadata={
            "order_id": order_id,
            "payment_id": payment_id,
            "order_number": str(order.order_number),
            "location_id": order.location_id,
        },
    )
    return {
        "payment_intent_id": str(intent["id"]),
        "client_secret": str(intent["client_secret"]),
    }


def pay_web_order_card(
    session: Session,
    stripe: StripeApi,
    *,
    order_id: str,
    payment_id: str,
    stripe_payment_intent_id: str,
    tip_cents: int = 0,
) -> PaymentOutcome:
    """Record (and capture) the card payment for the whole remaining balance of a web
    order, through its location's web device. Releases the card if the order can't take
    it, exactly as the till flow does."""
    order = get_web_order(session, order_id)
    device = web_device(session, order.location_id, for_update=True)
    provider = StripeProvider(stripe, stripe_payment_intent_id, payment_id)
    owing = balance_cents(session, order_id)
    try:
        return pay_order(
            session,
            device,
            order_id,
            provider,
            _command(device),
            payment_id=payment_id,
            amount_cents=owing,
            tip_cents=tip_cents,
            currency="AUD",
        )
    except VoidPosError as exc:
        if exc.code in RELEASE_ON:
            release(stripe, stripe_payment_intent_id)
        raise
