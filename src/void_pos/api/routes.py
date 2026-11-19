"""Till commands (orders, kitchen, payments), sync, export and reports."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..balance import balance_cents
from ..clock import local_date
from ..commands import Command
from ..config import Settings, get_settings
from ..devices import get_location
from ..errors import Conflict, InvalidRequest, ServiceUnavailable, VoidPosError
from ..events.payloads import AdjustmentIn
from ..export import export_csv, export_location
from ..models import Order, Payment
from ..orders import (
    UNSETTLED,
    add_lines,
    adjust_order,
    get_order,
    list_orders,
    order_view,
    order_views,
    place_order,
    remove_adjustment,
    remove_lines,
    send_to_kitchen,
    void_order,
)
from ..payments import (
    CashProvider,
    ExternalProvider,
    PaymentProvider,
    pay_order,
    refund_payment,
)
from ..payments.stripe import RELEASE_ON, StripeApi, StripeProvider, release
from ..printing import Printer, Rasterizer
from ..receipts import build_receipt, encode_receipt
from ..reporting import shift_closeout
from ..sync import MAX_PULL, pull_events, push_events
from .approval import PinLimiterDep, approve
from .auth import AdminDep, DeviceDep, PrincipalDep
from .deps import get_printer, get_rasterizer, get_receipt_printer, get_session, get_stripe
from .schemas import (
    Ack,
    AddLinesRequest,
    AdjustRequest,
    CommandIn,
    KitchenRequest,
    KitchenResponse,
    OrderResponse,
    OrderSummary,
    PaymentResponse,
    PayRequest,
    PlaceOrderRequest,
    PullResponse,
    PushRequest,
    PushResponse,
    ReceiptOut,
    ReceiptPrinted,
    RefundRequest,
    RemoveLinesRequest,
    StripeConfigOut,
    StripeIntentOut,
    StripeIntentRequest,
    VoidRequest,
    VoidResponse,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
PrinterDep = Annotated[Printer | None, Depends(get_printer)]
RasterizerDep = Annotated[Rasterizer | None, Depends(get_rasterizer)]
ReceiptPrinterDep = Annotated[Printer | None, Depends(get_receipt_printer)]
StripeDep = Annotated[StripeApi | None, Depends(get_stripe)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
OrderState = Literal["open", "sent", "paid", "voided", "refunded"]


def _command(body: CommandIn) -> Command:
    return Command(event_id=body.event_id, seq=body.seq, occurred_at=body.occurred_at)


def _order_response(session: Session, order: Order, *, replayed: bool) -> OrderResponse:
    return OrderResponse.of_view(order_view(session, order), replayed=replayed)


@router.post("/orders", response_model=OrderResponse, tags=["orders"])
def place_order_route(req: PlaceOrderRequest, session: SessionDep, device: DeviceDep) -> OrderResponse:
    outcome = place_order(session, device, _command(req), req, business_date=req.business_date)
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.get(
    "/locations/{location_id}/orders", response_model=list[OrderSummary], tags=["orders"]
)
def read_orders(
    location_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    state: Annotated[list[OrderState] | None, Query()] = None,
    business_date: date | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[OrderSummary]:
    """A location's orders, newest first: by default the unsettled ones (open or sent,
    whatever the date), so a till can pick a table back up."""
    principal.require_location(location_id)
    orders = list_orders(
        session,
        location_id,
        states=state or UNSETTLED,
        business_date=business_date,
        limit=limit,
    )
    return [OrderSummary.of(view) for view in order_views(session, orders)]


@router.post("/orders/{order_id}/lines", response_model=OrderResponse, tags=["orders"])
def add_lines_route(
    order_id: str, body: AddLinesRequest, session: SessionDep, device: DeviceDep
) -> OrderResponse:
    """Add items to a placed order; send to the kitchen again to print just those."""
    outcome = add_lines(session, device, order_id, _command(body), body.lines)
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.get("/orders/{order_id}/receipt", response_model=ReceiptOut, tags=["orders"])
def read_receipt(order_id: str, session: SessionDep, principal: PrincipalDep) -> ReceiptOut:
    """The customer receipt (a tax invoice once the venue has an ABN), to show or print."""
    order = get_order(session, order_id, location_id=principal.location_id)
    receipt = build_receipt(session, order_view(session, order))
    return ReceiptOut.model_validate(asdict(receipt))


@router.post("/orders/{order_id}/print-receipt", response_model=ReceiptPrinted, tags=["orders"])
def print_receipt(
    order_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    printer: ReceiptPrinterDep,
    rasterizer: RasterizerDep,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReceiptPrinted:
    """Print the customer receipt on the receipt printer. Nothing is recorded, so it can
    be printed again as often as needed."""
    order = get_order(session, order_id, location_id=principal.location_id)
    if printer is None:
        raise ServiceUnavailable(
            "no receipt printer is set up; show the receipt on screen instead",
            code="no_receipt_printer",
        )
    receipt = build_receipt(session, order_view(session, order))
    data = encode_receipt(
        receipt, columns=settings.receipt_printer_columns, rasterizer=rasterizer
    )
    printer.send(data)
    return ReceiptPrinted(printed=True, ticket_bytes=len(data))


@router.get("/orders/{order_id}", response_model=OrderResponse, tags=["orders"])
def read_order(order_id: str, session: SessionDep, principal: PrincipalDep) -> OrderResponse:
    order = get_order(session, order_id, location_id=principal.location_id)
    return _order_response(session, order, replayed=False)


@router.post("/orders/{order_id}/send-to-kitchen", response_model=KitchenResponse, tags=["orders"])
def send_to_kitchen_route(
    order_id: str,
    body: KitchenRequest,
    session: SessionDep,
    device: DeviceDep,
    printer: PrinterDep,
    rasterizer: RasterizerDep,
) -> KitchenResponse:
    outcome = send_to_kitchen(
        session, device, order_id, _command(body), printer=printer, rasterizer=rasterizer
    )
    session.commit()
    return KitchenResponse(
        order_id=order_id,
        state=outcome.order.state,
        printed=outcome.printed,
        ticket_bytes=outcome.ticket_bytes,
        lines_sent=outcome.lines_sent,
        replayed=outcome.replayed,
    )


@router.post("/orders/{order_id}/void", response_model=VoidResponse, tags=["orders"])
def void_route(
    order_id: str,
    body: VoidRequest,
    session: SessionDep,
    device: DeviceDep,
    printer: PrinterDep,
    rasterizer: RasterizerDep,
    pins: PinLimiterDep,
) -> VoidResponse:
    manager = approve(session, device, body.manager_pin, pins)
    outcome = void_order(
        session,
        device,
        order_id,
        _command(body),
        reason=body.reason,
        printer=printer,
        rasterizer=rasterizer,
        approved_by=manager,
    )
    session.commit()
    return VoidResponse(
        order_id=order_id,
        state=outcome.order.state,
        printed=outcome.printed,
        replayed=outcome.replayed,
    )


@router.post("/orders/{order_id}/pay", response_model=PaymentResponse, tags=["orders"])
def pay_route(
    order_id: str, body: PayRequest, session: SessionDep, device: DeviceDep, stripe: StripeDep
) -> PaymentResponse:
    provider: PaymentProvider
    if body.method == "stripe":
        provider = StripeProvider(
            _stripe_or_503(stripe), body.stripe_payment_intent_id or "", body.payment_id
        )
    elif body.method == "external":
        provider = ExternalProvider(external_ref=body.external_ref or "")
    else:
        provider = CashProvider()
    try:
        outcome = pay_order(
            session,
            device,
            order_id,
            provider,
            _command(body),
            payment_id=body.payment_id,
            amount_cents=body.amount_cents,
            tip_cents=body.tip_cents,
            currency=body.currency,
        )
    except VoidPosError as exc:
        if isinstance(provider, StripeProvider) and exc.code in RELEASE_ON:
            release(provider.api, provider.intent_id)
        raise
    session.commit()
    payment = outcome.payment
    return PaymentResponse(
        order_id=order_id,
        payment_id=payment.id,
        provider=payment.provider,
        amount_cents=payment.amount_cents,
        tip_cents=payment.tip_cents,
        currency=payment.currency,
        status=payment.status,
        order_state=outcome.order.state,
        balance_cents=outcome.balance_cents,
        replayed=outcome.replayed,
    )


def _stripe_or_503(stripe: StripeApi | None) -> StripeApi:
    if stripe is None:
        raise ServiceUnavailable(
            "card payments through Stripe aren't set up on this server", code="stripe_not_set_up"
        )
    return stripe


def _refund_provider(
    session: Session, payment_id: str, external_ref: str | None, stripe: StripeApi | None
) -> PaymentProvider:
    """Refund through the payment's own provider."""
    payment = session.get(Payment, payment_id)
    if payment is not None and payment.provider == "external":
        return ExternalProvider(external_ref=external_ref or "")
    if payment is not None and payment.provider == "stripe":
        return StripeProvider(_stripe_or_503(stripe))
    return CashProvider()


@router.get("/payments/stripe", response_model=StripeConfigOut, tags=["payments"])
def stripe_config(settings: SettingsDep, _: PrincipalDep) -> StripeConfigOut:
    """Whether the till can take cards through Stripe, and the key its browser uses."""
    return StripeConfigOut(
        enabled=settings.stripe_enabled, publishable_key=settings.stripe_publishable_key
    )


@router.post(
    "/orders/{order_id}/stripe-intent", response_model=StripeIntentOut, tags=["payments"]
)
def stripe_intent(
    order_id: str,
    body: StripeIntentRequest,
    session: SessionDep,
    device: DeviceDep,
    stripe: StripeDep,
    settings: SettingsDep,
) -> StripeIntentOut:
    """Start a card payment for part or all of what's owing (plus any tip). Nothing is
    recorded until the till confirms it with POST /orders/{id}/pay, method "stripe"."""
    api = _stripe_or_503(stripe)
    order = get_order(session, order_id, location_id=device.location_id)
    if order.state not in UNSETTLED:
        raise Conflict(f"this order is already {order.state}", code="order_closed")
    owing = balance_cents(session, order_id)
    if body.amount_cents > owing:
        raise InvalidRequest(
            f"the payment of {body.amount_cents} cents is more than the {owing} cents owing",
            code="amount_exceeds_balance",
            balance_cents=owing,
        )
    intent = api.create_intent(
        amount_cents=body.amount_cents + body.tip_cents,
        currency="aud",
        idempotency_key=f"intent-{body.payment_id}",
        metadata={
            "order_id": order_id,
            "payment_id": body.payment_id,
            "order_number": str(order.order_number),
            "location_id": order.location_id,
        },
    )
    return StripeIntentOut(
        payment_intent_id=str(intent["id"]),
        client_secret=str(intent["client_secret"]),
        publishable_key=settings.stripe_publishable_key or "",
    )


@router.post("/orders/{order_id}/remove-lines", response_model=OrderResponse, tags=["orders"])
def remove_lines_route(
    order_id: str,
    body: RemoveLinesRequest,
    session: SessionDep,
    device: DeviceDep,
    printer: PrinterDep,
    rasterizer: RasterizerDep,
    pins: PinLimiterDep,
) -> OrderResponse:
    """Take items off an open order (a CANCEL ticket prints for any the kitchen has). To
    change a quantity, take the item off and add it again."""
    outcome = remove_lines(
        session,
        device,
        order_id,
        _command(body),
        line_ids=body.line_ids,
        reason=body.reason,
        approver=lambda: approve(session, device, body.manager_pin, pins),
        printer=printer,
        rasterizer=rasterizer,
    )
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.post("/orders/{order_id}/adjustments", response_model=OrderResponse, tags=["orders"])
def adjust_route(
    order_id: str, body: AdjustRequest, session: SessionDep, device: DeviceDep, pins: PinLimiterDep
) -> OrderResponse:
    """A discount or surcharge on the whole order."""
    adjustment = AdjustmentIn.model_validate(body.model_dump(include=set(AdjustmentIn.model_fields)))
    outcome = adjust_order(
        session,
        device,
        order_id,
        _command(body),
        adjustment,
        approver=lambda: approve(session, device, body.manager_pin, pins),
    )
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.post(
    "/orders/{order_id}/adjustments/{adjustment_id}/remove",
    response_model=OrderResponse,
    tags=["orders"],
)
def remove_adjustment_route(
    order_id: str, adjustment_id: str, body: CommandIn, session: SessionDep, device: DeviceDep
) -> OrderResponse:
    outcome = remove_adjustment(session, device, order_id, _command(body), adjustment_id)
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.post("/orders/{order_id}/refunds", response_model=OrderResponse, tags=["orders"])
def refund_route(
    order_id: str,
    body: RefundRequest,
    session: SessionDep,
    device: DeviceDep,
    pins: PinLimiterDep,
    stripe: StripeDep,
) -> OrderResponse:
    """Give money back against a payment of a paid order, through its own provider, with
    a manager's PIN once the venue has a manager."""
    outcome = refund_payment(
        session,
        device,
        order_id,
        _refund_provider(session, body.payment_id, body.external_ref, stripe),
        _command(body),
        refund_id=body.refund_id,
        payment_id=body.payment_id,
        amount_cents=body.amount_cents,
        reason=body.reason,
        approver=lambda: approve(session, device, body.manager_pin, pins),
    )
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


@router.post("/sync/push", response_model=PushResponse, tags=["sync"])
def sync_push(req: PushRequest, session: SessionDep, device: DeviceDep) -> PushResponse:
    results = push_events(session, [event.model_dump() for event in req.events], device=device)
    session.commit()
    return PushResponse(
        acks=[Ack(event_id=r.event_id, status=r.status, detail=r.detail, code=r.code) for r in results]
    )


@router.get("/sync/pull", response_model=PullResponse, tags=["sync"])
def sync_pull(
    session: SessionDep,
    device: DeviceDep,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PULL)] = 100,
    include_own: bool = False,
) -> PullResponse:
    """Events at this till's location after ``cursor``, other tills' only by default."""
    result = pull_events(
        session,
        location_id=device.location_id,
        cursor=cursor,
        limit=limit,
        exclude_device_id=None if include_own else device.id,
    )
    session.commit()  # keep the sync positions handed out while pulling
    return PullResponse(
        events=result.events, next_cursor=result.next_cursor, has_more=result.has_more
    )


@router.get("/export/{location_id}", tags=["export"])
def export_data(location_id: str, session: SessionDep, _: AdminDep) -> dict[str, Any]:
    return export_location(session, location_id)


@router.get("/export/{location_id}/csv/{entity}", tags=["export"])
def export_entity_csv(location_id: str, entity: str, session: SessionDep, _: AdminDep) -> Response:
    csv_text = export_csv(session, location_id, entity)
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{entity}.csv"'},
    )


@router.get("/reports/{location_id}/closeout", tags=["reports"])
def closeout(
    location_id: str,
    session: SessionDep,
    principal: PrincipalDep,
    business_date: date | None = None,
) -> dict[str, Any]:
    """The shift closeout for a business date (default: today in the location's zone)."""
    principal.require_location(location_id)
    location = get_location(session, location_id)
    return shift_closeout(session, location_id, business_date or local_date(location.timezone))
