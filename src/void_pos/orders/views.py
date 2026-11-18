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

from ..models import Order, OrderAdjustment, OrderLine, Payment, Refund
from ..totals import order_math

UNSETTLED = ("open", "sent")
SETTLED_SALES = ("paid", "refunded")


@dataclass(frozen=True)
class OrderView:
    order: Order
    lines: list[OrderLine]  # every line, removed ones included (marked)
    adjustments: list[OrderAdjustment]  # the ones in force
    adjustment_cents: dict[str, int]  # adjustment id -> signed cents
    payments: list[Payment]
    refunds: list[Refund]
    subtotal_cents: int
    total_cents: int
    gst_cents: int
    balance_cents: int

    @property
    def active_lines(self) -> list[OrderLine]:
        return [line for line in self.lines if line.removed_at is None]

    @property
    def unsent_lines(self) -> int:
        return sum(1 for line in self.active_lines if line.sent_at is None)

    @property
    def tips_cents(self) -> int:
        return sum(p.tip_cents for p in self.payments if p.status == "captured")

    @property
    def refunded_cents(self) -> int:
        return sum(r.amount_cents for r in self.refunds)


def order_views(session: Session, orders: Sequence[Order]) -> list[OrderView]:
    ids = [o.id for o in orders]
    lines: dict[str, list[OrderLine]] = defaultdict(list)
    adjustments: dict[str, list[OrderAdjustment]] = defaultdict(list)
    payments: dict[str, list[Payment]] = defaultdict(list)
    refunds: dict[str, list[Refund]] = defaultdict(list)
    if ids:
        for line in session.scalars(
            select(OrderLine).where(OrderLine.order_id.in_(ids)).order_by(OrderLine.id)
        ):
            lines[line.order_id].append(line)
        for adj in session.scalars(
            select(OrderAdjustment)
            .where(OrderAdjustment.order_id.in_(ids), OrderAdjustment.removed_at.is_(None))
            .order_by(OrderAdjustment.id)
        ):
            adjustments[adj.order_id].append(adj)
        for payment in session.scalars(
            select(Payment).where(Payment.order_id.in_(ids)).order_by(Payment.id)
        ):
            payments[payment.order_id].append(payment)
        for refund in session.scalars(
            select(Refund).where(Refund.order_id.in_(ids)).order_by(Refund.id)
        ):
            refunds[refund.order_id].append(refund)
    views = []
    for order in orders:
        active = [line for line in lines[order.id] if line.removed_at is None]
        money = order_math(active, adjustments[order.id])
        paid = sum(p.amount_cents for p in payments[order.id] if p.status == "captured")
        views.append(
            OrderView(
                order=order,
                lines=lines[order.id],
                adjustments=adjustments[order.id],
                adjustment_cents=money.adjustments,
                payments=payments[order.id],
                refunds=refunds[order.id],
                subtotal_cents=money.subtotal_cents,
                total_cents=money.total_cents,
                gst_cents=money.gst_cents,
                balance_cents=max(money.total_cents - paid, 0),
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
    limit: int | None = 200,
) -> list[Order]:
    """A location's orders in the given states, newest first (all of them with no limit)."""
    stmt = select(Order).where(Order.location_id == location_id, Order.state.in_(states))
    if business_date is not None:
        stmt = stmt.where(Order.business_date == business_date)
    stmt = stmt.order_by(Order.id.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.scalars(stmt).all())
