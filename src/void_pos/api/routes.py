"""Till commands (orders, kitchen, payments), sync, export and reports."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ..clock import local_date
from ..commands import Command
from ..devices import get_location
from ..export import export_csv, export_location
from ..models import Order
from ..orders import get_order, place_order, send_to_kitchen, void_order
from ..payments import (
    CashProvider,
    ExternalProvider,
    PaymentProvider,
    balance_cents,
    order_total_cents,
    pay_order,
)
from ..printing import Printer, Rasterizer
from ..reporting import shift_closeout
from ..sync import MAX_PULL, pull_events, push_events
from .approval import PinLimiterDep, approve
from .auth import AdminDep, DeviceDep, PrincipalDep
from .deps import get_printer, get_rasterizer, get_session
from .schemas import (
    Ack,
    CommandIn,
    KitchenRequest,
    KitchenResponse,
    OrderResponse,
    PaymentResponse,
    PayRequest,
    PlaceOrderRequest,
    PullResponse,
    PushRequest,
    PushResponse,
    VoidRequest,
    VoidResponse,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
PrinterDep = Annotated[Printer | None, Depends(get_printer)]
RasterizerDep = Annotated[Rasterizer | None, Depends(get_rasterizer)]


def _command(body: CommandIn) -> Command:
    return Command(event_id=body.event_id, seq=body.seq, occurred_at=body.occurred_at)


def _order_response(session: Session, order: Order, *, replayed: bool) -> OrderResponse:
    return OrderResponse(
        id=order.id,
        order_number=order.order_number,
        state=order.state,
        business_date=order.business_date,
        total_cents=order_total_cents(session, order.id),
        balance_cents=balance_cents(session, order.id),
        replayed=replayed,
    )


@router.post("/orders", response_model=OrderResponse, tags=["orders"])
def place_order_route(req: PlaceOrderRequest, session: SessionDep, device: DeviceDep) -> OrderResponse:
    outcome = place_order(session, device, _command(req), req, business_date=req.business_date)
    session.commit()
    return _order_response(session, outcome.order, replayed=outcome.replayed)


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
    order_id: str, body: PayRequest, session: SessionDep, device: DeviceDep
) -> PaymentResponse:
    provider: PaymentProvider = (
        ExternalProvider(external_ref=body.external_ref or "")
        if body.method == "external"
        else CashProvider()
    )
    outcome = pay_order(
        session,
        device,
        order_id,
        provider,
        _command(body),
        payment_id=body.payment_id,
        amount_cents=body.amount_cents,
        currency=body.currency,
    )
    session.commit()
    payment = outcome.payment
    return PaymentResponse(
        order_id=order_id,
        payment_id=payment.id,
        provider=payment.provider,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
        status=payment.status,
        order_state=outcome.order.state,
        balance_cents=outcome.balance_cents,
        replayed=outcome.replayed,
    )


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
