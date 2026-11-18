from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import Order, OrderLine
from .escpos import encode_ticket
from .raster import Rasterizer
from .ticket import build_kitchen_ticket
from .transport import Printer


def print_order(
    session: Session,
    order_id: str,
    printer: Printer,
    rasterizer: Rasterizer | None = None,
    *,
    void_reason: str | None = None,
    line_ids: list[str] | None = None,
    heading: str = "ORDER",
) -> bytes:
    """Build the kitchen ticket for an order and send it to the printer. With
    ``void_reason`` it is a VOID ticket telling the kitchen to stop the order.

    Returns the raw ESC/POS bytes so callers can log or test them.
    """
    order = session.get(Order, order_id)
    if order is None:
        raise NotFound("order not found")
    lines = session.scalars(
        select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
    ).all()
    if line_ids is not None:
        wanted = set(line_ids)
        lines = [line for line in lines if line.id in wanted]
    else:
        lines = [line for line in lines if line.removed_at is None]
    ticket = build_kitchen_ticket(order, lines, void_reason=void_reason, heading=heading)
    data = encode_ticket(ticket, rasterizer=rasterizer)
    printer.send(data)
    return data
