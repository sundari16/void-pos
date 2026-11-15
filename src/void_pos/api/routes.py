from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ..events import ingest_event
from ..export import CSV_ENTITIES, export_csv, export_location
from ..ids import new_ulid
from ..models import Device, Order
from ..payments import CashProvider, ExternalProvider, pay_order
from ..printing import Printer, Rasterizer, print_order
from ..sync import pull_events, push_events
from .deps import get_printer, get_rasterizer, get_session
from .schemas import (
    KitchenResponse,
    OrderResponse,
    PaymentResponse,
    PayRequest,
    PlaceOrderRequest,
    PullResponse,
    PushRequest,
    PushResponse,
)

router = APIRouter()

SessionDep = Annotated[Session, Depends(get_session)]
PrinterDep = Annotated[Printer, Depends(get_printer)]
RasterizerDep = Annotated[Rasterizer | None, Depends(get_rasterizer)]


@router.post("/orders", response_model=OrderResponse)
def place_order(req: PlaceOrderRequest, session: SessionDep) -> OrderResponse:
    device = session.get(Device, req.device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    order_id = new_ulid()
    ref_to_id = {line.ref: new_ulid() for line in req.lines}
    payload_lines = [
        {
            "id": ref_to_id[line.ref],
            "parent_line_id": ref_to_id.get(line.parent_ref) if line.parent_ref else None,
            "menu_item_id": line.menu_item_id,
            "modifier_id": line.modifier_id,
            "quantity": line.quantity,
            "name_snapshot": line.name_snapshot,
            "kitchen_name_snapshot": line.kitchen_name_snapshot,
            "unit_price_cents": line.unit_price_cents,
            "tax_rate_snapshot": line.tax_rate_snapshot,
            "category_snapshot": line.category_snapshot,
        }
        for line in req.lines
    ]
    ingest_event(
        session,
        {
            "event_id": new_ulid(),
            "device_id": device.id,
            "seq": device.last_seq + 1,
            "location_id": req.location_id,
            "business_date": req.business_date or datetime.now(UTC).date(),
            "occurred_at": datetime.now(UTC),
            "type": "order.placed",
            "payload": {
                "order_id": order_id,
                "order_number": req.order_number,
                "customer_id": req.customer_id,
                "channel": req.channel,
                "fulfillment": req.fulfillment,
                "lines": payload_lines,
            },
        },
    )
    session.commit()
    order = session.get(Order, order_id)
    return OrderResponse(id=order.id, order_number=order.order_number, state=order.state)


@router.post("/orders/{order_id}/send-to-kitchen", response_model=KitchenResponse)
def send_to_kitchen(
    order_id: str,
    session: SessionDep,
    printer: PrinterDep,
    rasterizer: RasterizerDep,
) -> KitchenResponse:
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    device = session.get(Device, order.device_id)
    ingest_event(
        session,
        {
            "event_id": new_ulid(),
            "device_id": order.device_id,
            "seq": (device.last_seq + 1) if device is not None else 1,
            "location_id": order.location_id,
            "business_date": order.business_date,
            "occurred_at": datetime.now(UTC),
            "type": "order.sent",
            "payload": {"order_id": order_id},
        },
    )
    data = print_order(session, order_id, printer, rasterizer)
    session.commit()
    session.refresh(order)
    return KitchenResponse(order_id=order_id, state=order.state, ticket_bytes=len(data))


@router.post("/orders/{order_id}/pay", response_model=PaymentResponse)
def pay(order_id: str, body: PayRequest, session: SessionDep) -> PaymentResponse:
    if body.method == "cash":
        provider = CashProvider()
    elif body.method == "external":
        if not body.external_ref:
            raise HTTPException(
                status_code=422, detail="external_ref required for method 'external'"
            )
        provider = ExternalProvider(external_ref=body.external_ref)
    else:
        raise HTTPException(status_code=422, detail=f"unknown payment method {body.method!r}")

    try:
        payment = pay_order(
            session, order_id, provider, amount_cents=body.amount_cents, currency=body.currency
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc

    session.commit()
    order = session.get(Order, order_id)
    return PaymentResponse(
        order_id=order_id,
        payment_id=payment.id,
        provider=payment.provider,
        amount_cents=payment.amount_cents,
        status=payment.status,
        order_state=order.state,
    )


@router.post("/sync/push", response_model=PushResponse)
def sync_push(req: PushRequest, session: SessionDep) -> PushResponse:
    result = push_events(session, [event.model_dump() for event in req.events])
    session.commit()
    return PushResponse(acks=result.acks, cursor=result.cursor)


@router.get("/sync/pull", response_model=PullResponse)
def sync_pull(
    session: SessionDep,
    cursor: str | None = None,
    limit: int = 100,
    device_id: str | None = None,
) -> PullResponse:
    result = pull_events(session, cursor, limit=limit, exclude_device_id=device_id)
    return PullResponse(
        events=result.events, next_cursor=result.next_cursor, has_more=result.has_more
    )


@router.get("/export/{location_id}")
def export_data(location_id: str, session: SessionDep) -> dict[str, Any]:
    try:
        return export_location(session, location_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc


@router.get("/export/{location_id}/csv/{entity}")
def export_entity_csv(location_id: str, entity: str, session: SessionDep) -> Response:
    try:
        csv_text = export_csv(session, location_id, entity)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="location not found") from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown entity {entity!r}; one of {list(CSV_ENTITIES)}"
        ) from exc
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{entity}.csv"'},
    )
