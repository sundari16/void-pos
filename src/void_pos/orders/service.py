"""Order commands from a paired till: place an order and send it to the kitchen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import local_date
from ..commands import Command, ensure_seq_free, find_replay, record
from ..devices import get_location
from ..errors import Conflict, NotFound
from ..events import EventType, OrderPlaced
from ..events.types import PAID, SENT, VOIDED
from ..models import Device, Order, Payment
from ..printing import Printer, Rasterizer, print_order
from .rules import check_order_lines


@dataclass(frozen=True)
class OrderOutcome:
    order: Order
    replayed: bool


@dataclass(frozen=True)
class VoidOutcome:
    order: Order
    printed: bool
    replayed: bool


@dataclass(frozen=True)
class KitchenOutcome:
    order: Order
    printed: bool
    ticket_bytes: int
    replayed: bool


def get_order(
    session: Session, order_id: str, *, location_id: str | None = None, for_update: bool = False
) -> Order:
    """The order, scoped to a location when given (another venue's order is not found).
    ``for_update`` locks the row until commit (PostgreSQL), so two tills can't act on the
    same order at once."""
    order = session.get(Order, order_id, with_for_update=for_update)
    if order is None or (location_id is not None and order.location_id != location_id):
        raise NotFound("order not found")
    return order


def place_order(
    session: Session,
    device: Device,
    command: Command,
    order: OrderPlaced,
    *,
    business_date: date | None = None,
) -> OrderOutcome:
    """Place ``order`` as an ``order.placed`` event, if it follows the menu's rules (see
    ``rules``). The business date defaults to today in the location's own timezone."""
    if find_replay(
        session, command, device=device, event_type=EventType.ORDER_PLACED, order_id=order.order_id
    ):
        placed = get_order(session, order.order_id, location_id=device.location_id)
        return OrderOutcome(placed, replayed=True)
    if session.get(Order, order.order_id) is not None:
        raise Conflict("an order with this id already exists", code="order_exists")

    location = get_location(session, device.location_id)
    check_order_lines(session, location, order.lines)
    day = business_date or local_date(location.timezone)
    payload = order.model_dump(mode="json", include=set(OrderPlaced.model_fields))
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_PLACED,
        payload=payload,
        business_date=day,
    )
    return OrderOutcome(get_order(session, order.order_id), replayed=False)


def send_to_kitchen(
    session: Session,
    device: Device,
    order_id: str,
    command: Command,
    *,
    printer: Printer | None,
    rasterizer: Rasterizer | None = None,
) -> KitchenOutcome:
    """Record ``order.sent`` and print the kitchen ticket.

    Sending again reprints, and a paid order still prints (counter service takes payment
    first); only a voided order is refused. A replayed command does not reprint. The
    ticket prints before the event is written: if the printer can't be reached nothing
    is recorded and the till can simply retry, and the event's flush-to-commit window
    stays as short as any other command's (sync pull orders events by arrival).
    """
    order = get_order(session, order_id, location_id=device.location_id, for_update=True)
    if find_replay(
        session, command, device=device, event_type=EventType.ORDER_SENT, order_id=order_id
    ):
        return KitchenOutcome(order, printed=False, ticket_bytes=0, replayed=True)
    if order.state == VOIDED:
        raise Conflict("this order is voided", code="order_voided")

    ensure_seq_free(session, device, command)
    data = print_order(session, order_id, printer, rasterizer) if printer is not None else None
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_SENT,
        payload={"order_id": order_id},
        business_date=order.business_date,
    )
    if data is None:
        return KitchenOutcome(order, printed=False, ticket_bytes=0, replayed=False)
    return KitchenOutcome(order, printed=True, ticket_bytes=len(data), replayed=False)


def void_order(
    session: Session,
    device: Device,
    order_id: str,
    command: Command,
    *,
    reason: str,
    printer: Printer | None,
    rasterizer: Rasterizer | None = None,
) -> VoidOutcome:
    """Record ``order.voided``. An order with payments is refused (refund first), and so
    is one already voided. If the kitchen has the order, a VOID ticket prints first, so
    a printer that can't be reached leaves nothing recorded and the till can retry."""
    order = get_order(session, order_id, location_id=device.location_id, for_update=True)
    if find_replay(
        session, command, device=device, event_type=EventType.ORDER_VOIDED, order_id=order_id
    ):
        return VoidOutcome(order, printed=False, replayed=True)
    if order.state == VOIDED:
        raise Conflict("this order is already voided", code="order_voided")
    paid = session.scalar(
        select(func.count())
        .select_from(Payment)
        .where(Payment.order_id == order_id, Payment.status == "captured")
    )
    if order.state == PAID or paid:
        raise Conflict(
            "this order has a payment; refund it before voiding", code="order_paid"
        )

    ensure_seq_free(session, device, command)
    printed = False
    if order.state == SENT and printer is not None:
        print_order(session, order_id, printer, rasterizer, void_reason=reason)
        printed = True
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_VOIDED,
        payload={"order_id": order_id, "reason": reason},
        business_date=order.business_date,
    )
    return VoidOutcome(order, printed=printed, replayed=False)
