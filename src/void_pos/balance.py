"""What an order costs and what is still owing on it.

An order can be paid in parts (split bills, cash plus card). It counts as paid once its
captured payments cover the total. Kept free of the payment service so the event
projection can use it too.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OrderAdjustment, OrderLine, Payment, Refund
from .totals import OrderMath, order_math


def active_lines(session: Session, order_id: str) -> list[OrderLine]:
    """The order's lines that count: everything not taken off."""
    return list(
        session.scalars(
            select(OrderLine)
            .where(OrderLine.order_id == order_id, OrderLine.removed_at.is_(None))
            .order_by(OrderLine.id)
        )
    )


def active_adjustments(session: Session, order_id: str) -> list[OrderAdjustment]:
    return list(
        session.scalars(
            select(OrderAdjustment)
            .where(OrderAdjustment.order_id == order_id, OrderAdjustment.removed_at.is_(None))
            .order_by(OrderAdjustment.id)
        )
    )


def order_money(session: Session, order_id: str) -> OrderMath:
    return order_math(active_lines(session, order_id), active_adjustments(session, order_id))


def order_total_cents(session: Session, order_id: str) -> int:
    """The items (modifiers per unit of their item), less discounts, plus surcharges."""
    return order_money(session, order_id).total_cents


def captured_cents(session: Session, order_id: str) -> int:
    paid = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents), 0)).where(
            Payment.order_id == order_id, Payment.status == "captured"
        )
    )
    return int(paid or 0)


def balance_cents(session: Session, order_id: str) -> int:
    """The total less captured payments; never below zero."""
    return max(order_total_cents(session, order_id) - captured_cents(session, order_id), 0)


def refunded_cents(session: Session, order_id: str) -> int:
    given = session.scalar(
        select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(Refund.order_id == order_id)
    )
    return int(given or 0)
