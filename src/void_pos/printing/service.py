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
) -> bytes:
    """Build the kitchen ticket for an order and send it to the printer.

    Returns the raw ESC/POS bytes so callers can log or test them.
    """
    order = session.get(Order, order_id)
    if order is None:
        raise NotFound("order not found")
    lines = session.scalars(
        select(OrderLine).where(OrderLine.order_id == order_id).order_by(OrderLine.id)
    ).all()
    data = encode_ticket(build_kitchen_ticket(order, lines), rasterizer=rasterizer)
    printer.send(data)
    return data
