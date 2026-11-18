"""What an order costs and what is still owing on it.

An order can be paid in parts (split bills, cash plus card). It counts as paid once its
captured payments cover the total. Kept free of the payment service so the event
projection can use it too.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OrderLine, Payment
from .totals import total_cents


def order_total_cents(session: Session, order_id: str) -> int:
    """Every line's price times its extended quantity (a modifier counts once per item)."""
    return total_cents(session.scalars(select(OrderLine).where(OrderLine.order_id == order_id)))


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
