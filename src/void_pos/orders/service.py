"""Order commands from a paired till: place an order and send it to the kitchen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import local_date
from ..commands import Command, ensure_seq_free, find_replay, record
from ..devices import get_location
from ..errors import Conflict, InvalidRequest, NotFound
from ..events import EventType, OrderPlaced
from ..events.payloads import MAX_ORDER_LINES, OrderLineIn
from ..events.types import PAID, VOIDED
from ..models import Device, Order, OrderLine, Payment, Staff
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
    lines_sent: int = 0  # 0: nothing new, so the ticket was a reprint


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


def add_lines(
    session: Session, device: Device, order_id: str, command: Command, lines: list[OrderLineIn]
) -> OrderOutcome:
    """Record ``order.lines_added``: more items for an open or sent order, checked against
    the menu like a new order. A paid order is closed (start a new one); a part-paid one
    takes more items and its balance grows."""
    order = get_order(session, order_id, location_id=device.location_id, for_update=True)
    if find_replay(
        session, command, device=device, event_type=EventType.ORDER_LINES_ADDED, order_id=order_id
    ):
        return OrderOutcome(order, replayed=True)
    if order.state == VOIDED:
        raise Conflict("this order is voided", code="order_voided")
    if order.state == PAID:
        raise Conflict(
            "this order is paid; start a new one", code="order_closed", order_state=order.state
        )
    ids = [line.id for line in lines]
    if session.scalar(select(OrderLine.id).where(OrderLine.id.in_(ids)).limit(1)) is not None:
        raise Conflict("one of these line ids is already used", code="line_exists")
    count = session.scalar(
        select(func.count()).select_from(OrderLine).where(OrderLine.order_id == order_id)
    )
    if (count or 0) + len(ids) > MAX_ORDER_LINES:
        raise InvalidRequest(
            f"an order can have at most {MAX_ORDER_LINES} lines; start another",
            code="order_too_long",
        )
    check_order_lines(session, get_location(session, device.location_id), lines)
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_LINES_ADDED,
        payload={"order_id": order_id, "lines": [line.model_dump(mode="json") for line in lines]},
        business_date=order.business_date,
    )
    return OrderOutcome(order, replayed=False)


def send_to_kitchen(
    session: Session,
    device: Device,
    order_id: str,
    command: Command,
    *,
    printer: Printer | None,
    rasterizer: Rasterizer | None = None,
) -> KitchenOutcome:
    """Record ``order.sent`` and print the kitchen ticket for the lines the kitchen hasn't
    had: the whole order the first time, then just the items added since ("ADDED").

    With nothing new, sending again reprints the whole order. A paid order still prints
    (counter service takes payment first); only a voided order is refused. A replayed
    command does not reprint. The
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
    lines = session.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).all()
    unsent = [line.id for line in lines if line.sent_at is None]
    if not unsent:
        heading, only = "REPRINT", None
    elif len(unsent) == len(lines):
        heading, only = "ORDER", None
    else:
        heading, only = "ADDED", unsent
    data = (
        print_order(session, order_id, printer, rasterizer, line_ids=only, heading=heading)
        if printer is not None
        else None
    )
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_SENT,
        payload={"order_id": order_id, "line_ids": unsent},
        business_date=order.business_date,
    )
    return KitchenOutcome(
        order,
        printed=data is not None,
        ticket_bytes=len(data) if data is not None else 0,
        replayed=False,
        lines_sent=len(unsent),
    )


def void_order(
    session: Session,
    device: Device,
    order_id: str,
    command: Command,
    *,
    reason: str,
    printer: Printer | None,
    rasterizer: Rasterizer | None = None,
    approved_by: Staff | None = None,
) -> VoidOutcome:
    """Record ``order.voided``. An order with payments is refused (refund first), and so
    is one already voided. If the kitchen has any of the order, a VOID ticket listing what
    it has prints first, so a printer that can't be reached leaves nothing recorded and
    the till can retry."""
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
    sent = list(
        session.scalars(
            select(OrderLine.id).where(
                OrderLine.order_id == order_id, OrderLine.sent_at.is_not(None)
            )
        )
    )
    if sent and printer is not None:
        print_order(session, order_id, printer, rasterizer, void_reason=reason, line_ids=sent)
        printed = True
    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_VOIDED,
        payload={
            "order_id": order_id,
            "reason": reason,
            **(
                {"approved_by": approved_by.id, "approved_by_name": approved_by.name}
                if approved_by is not None
                else {}
            ),
        },
        business_date=order.business_date,
    )
    return VoidOutcome(order, printed=printed, replayed=False)
