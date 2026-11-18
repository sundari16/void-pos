"""Orders as a till sees them: lines, payments, total, GST and what is still owing.

Built in batches, so a list of open orders costs a few queries, not a few per order.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Order, OrderLine, Payment
from ..totals import gst_cents, total_cents

UNSETTLED = ("open", "sent")


@dataclass(frozen=True)
class OrderView:
    order: Order
    lines: list[OrderLine]
    payments: list[Payment]
    total_cents: int
    gst_cents: int
    balance_cents: int

    @property
    def unsent_lines(self) -> int:
        return sum(1 for line in self.lines if line.sent_at is None)


def order_views(session: Session, orders: Sequence[Order]) -> list[OrderView]:
    ids = [o.id for o in orders]
    lines: dict[str, list[OrderLine]] = defaultdict(list)
    payments: dict[str, list[Payment]] = defaultdict(list)
    if ids:
        for line in session.scalars(
            select(OrderLine).where(OrderLine.order_id.in_(ids)).order_by(OrderLine.id)
        ):
            lines[line.order_id].append(line)
        for payment in session.scalars(
            select(Payment).where(Payment.order_id.in_(ids)).order_by(Payment.id)
        ):
            payments[payment.order_id].append(payment)
    views = []
    for order in orders:
        total = total_cents(lines[order.id])
        paid = sum(p.amount_cents for p in payments[order.id] if p.status == "captured")
        views.append(
            OrderView(
                order=order,
                lines=lines[order.id],
                payments=payments[order.id],
                total_cents=total,
                gst_cents=gst_cents(lines[order.id]),
                balance_cents=max(total - paid, 0),
            )
        )
    return views


def order_view(session: Session, order: Order) -> OrderView:
    return order_views(session, [order])[0]


def list_orders(
    session: Session,
    location_id: str,
    *,
    states: Sequence[str] = UNSETTLED,
    business_date: date | None = None,
    limit: int = 200,
) -> list[Order]:
    """A location's orders in the given states, newest first."""
    stmt = select(Order).where(Order.location_id == location_id, Order.state.in_(states))
    if business_date is not None:
        stmt = stmt.where(Order.business_date == business_date)
    return list(session.scalars(stmt.order_by(Order.id.desc()).limit(limit)).all())
