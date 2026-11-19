"""The public storefront API (``/online/*``): browse, order, pay, loyalty, history.

No bearer token — this is what a customer's browser calls. Orders go onto the ``web``
channel through the location's web device and into the same pipeline as the tills. The
whole router 404s when ``online_ordering_enabled`` is off.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..crm import customer_orders, get_customer
from ..devices import get_location
from ..errors import NotFound, ServiceUnavailable
from ..loyalty import balance as loyalty_balance
from ..loyalty import rules_from_settings
from ..menu import till_menu
from ..online import (
    get_web_order,
    pay_web_order_card,
    place_web_order,
    web_stripe_intent,
)
from ..orders import order_view, order_views
from ..payments.stripe import StripeApi
from .deps import get_session, get_stripe
from .online_schemas import (
    CustomerOrdersOut,
    LoyaltyBalanceOut,
    LoyaltyInfo,
    OnlineOrderOut,
    PlaceWebOrderRequest,
    StorefrontOut,
    WebOrderOut,
    WebPaymentOut,
    WebPayRequest,
    WebStripeIntentOut,
    WebStripeIntentRequest,
)
from .schemas import OrderSummary, TillMenuOut

SessionDep = Annotated[Session, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StripeDep = Annotated[StripeApi | None, Depends(get_stripe)]


def require_online(settings: SettingsDep) -> None:
    if not settings.online_ordering_enabled:
        raise NotFound("online ordering isn't available here")


router = APIRouter(prefix="/online", tags=["online"], dependencies=[Depends(require_online)])


def _stripe_or_503(stripe: StripeApi | None) -> StripeApi:
    if stripe is None:
        raise ServiceUnavailable(
            "online card payments aren't set up on this server", code="stripe_not_set_up"
        )
    return stripe


@router.get("/locations/{location_id}", response_model=StorefrontOut)
def storefront(location_id: str, session: SessionDep, settings: SettingsDep) -> StorefrontOut:
    """The location's menu plus what the storefront can do here (pay online, loyalty)."""
    location = get_location(session, location_id)
    return StorefrontOut(
        location_id=location.id,
        location_name=location.business_name or location.name,
        timezone=location.timezone,
        online_pay_enabled=settings.stripe_enabled,
        loyalty=LoyaltyInfo.of(rules_from_settings(settings)),
        menu=TillMenuOut.of(till_menu(session, location_id)),
    )


@router.post("/locations/{location_id}/orders", response_model=WebOrderOut)
def place(
    location_id: str, body: PlaceWebOrderRequest, session: SessionDep, settings: SettingsDep
) -> WebOrderOut:
    result = place_web_order(
        session,
        settings,
        location_id=location_id,
        order_id=body.order_id,
        name=body.customer.name,
        phone=body.customer.phone,
        email=body.customer.email,
        marketing_opt_in=body.customer.marketing_opt_in,
        fulfillment=body.fulfillment,
        delivery_address=body.delivery_address,
        customer_note=body.customer_note,
        lines=body.lines,
        redeem_points=body.redeem_points,
    )
    session.commit()
    return WebOrderOut.of(result, order_view(session, result.order))


@router.get("/orders/{order_id}", response_model=OnlineOrderOut)
def read_order(order_id: str, session: SessionDep) -> OnlineOrderOut:
    order = get_web_order(session, order_id)
    return OnlineOrderOut.of_web(order_view(session, order))


@router.post("/orders/{order_id}/stripe-intent", response_model=WebStripeIntentOut)
def stripe_intent(
    order_id: str,
    body: WebStripeIntentRequest,
    session: SessionDep,
    settings: SettingsDep,
    stripe: StripeDep,
) -> WebStripeIntentOut:
    """Start a card payment for the whole remaining balance of the order."""
    intent = web_stripe_intent(
        session,
        _stripe_or_503(stripe),
        order_id=order_id,
        payment_id=body.payment_id,
        tip_cents=body.tip_cents,
    )
    return WebStripeIntentOut(
        payment_intent_id=intent["payment_intent_id"],
        client_secret=intent["client_secret"],
        publishable_key=settings.stripe_publishable_key or "",
    )


@router.post("/orders/{order_id}/pay", response_model=WebPaymentOut)
def pay(
    order_id: str, body: WebPayRequest, session: SessionDep, stripe: StripeDep
) -> WebPaymentOut:
    """Record (and capture) the card payment for the order's remaining balance."""
    outcome = pay_web_order_card(
        session,
        _stripe_or_503(stripe),
        order_id=order_id,
        payment_id=body.payment_id,
        stripe_payment_intent_id=body.stripe_payment_intent_id,
        tip_cents=body.tip_cents,
    )
    session.commit()
    p = outcome.payment
    return WebPaymentOut(
        order_id=order_id,
        payment_id=p.id,
        provider=p.provider,
        amount_cents=p.amount_cents,
        tip_cents=p.tip_cents,
        currency=p.currency,
        status=p.status,
        order_state=outcome.order.state,
        balance_cents=outcome.balance_cents,
        replayed=outcome.replayed,
    )


@router.get("/customers/{customer_id}/orders", response_model=CustomerOrdersOut)
def customer_history(customer_id: str, session: SessionDep) -> CustomerOrdersOut:
    """A customer's own order history, by their opaque id."""
    orders = customer_orders(session, customer_id)
    return CustomerOrdersOut(
        customer_id=customer_id,
        orders=[OrderSummary.of(view) for view in order_views(session, orders)],
    )


@router.get("/customers/{customer_id}/loyalty", response_model=LoyaltyBalanceOut)
def customer_loyalty(
    customer_id: str, session: SessionDep, settings: SettingsDep
) -> LoyaltyBalanceOut:
    get_customer(session, customer_id)
    rules = rules_from_settings(settings)
    return LoyaltyBalanceOut(
        customer_id=customer_id,
        enabled=rules.enabled,
        balance=loyalty_balance(session, customer_id),
        earn_points_per_dollar=rules.earn_points_per_dollar,
        redeem_cents_per_point=rules.redeem_cents_per_point,
    )
