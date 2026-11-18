"""Sales reporting: shift closeouts and revenue-class sales for tip-outs.

Reports are keyed on ``business_date`` (not wall-clock), so an order rung up after
midnight on the previous business day still lands in the right shift. Finer shift windows
(a time range within a day) are a later refinement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import Location, Order, OrderLine, Payment


def payments_by_provider(session: Session, location_id: str, business_date: date) -> dict[str, int]:
    rows = session.execute(
        select(Payment.provider, func.sum(Payment.amount_cents))
        .where(
            Payment.location_id == location_id,
            Payment.business_date == business_date,
            Payment.status == "captured",
        )
        .group_by(Payment.provider)
    ).all()
    return {provider: int(total or 0) for provider, total in rows}


def revenue_class_sales(session: Session, location_id: str, business_date: date) -> dict[str, int]:
    """Net sales grouped by revenue class (the line's category) over paid orders — the
    basis for tip-outs. Lines with no category fall under ``uncategorized``."""
    rows = session.execute(
        select(
            OrderLine.category_snapshot,
            func.sum(OrderLine.unit_price_cents * OrderLine.quantity),
        )
        .join(Order, Order.id == OrderLine.order_id)
        .where(
            Order.location_id == location_id,
            Order.business_date == business_date,
            Order.state == "paid",
        )
        .group_by(OrderLine.category_snapshot)
    ).all()
    return {(category or "uncategorized"): int(total or 0) for category, total in rows}


def _order_counts(session: Session, location_id: str, business_date: date) -> dict[str, int]:
    rows = session.execute(
        select(Order.state, func.count())
        .where(Order.location_id == location_id, Order.business_date == business_date)
        .group_by(Order.state)
    ).all()
    by_state = {state: int(count) for state, count in rows}
    return {
        "total": sum(by_state.values()),
        "open": by_state.get("open", 0),
        "sent": by_state.get("sent", 0),
        "paid": by_state.get("paid", 0),
        "voided": by_state.get("voided", 0),
    }


def shift_closeout(session: Session, location_id: str, business_date: date) -> dict[str, Any]:
    """End-of-shift reconciliation for a location and business date."""
    if session.get(Location, location_id) is None:
        raise NotFound("location not found")

    by_provider = payments_by_provider(session, location_id, business_date)
    return {
        "location_id": location_id,
        "business_date": business_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "orders": _order_counts(session, location_id, business_date),
        "gross_sales_cents": sum(by_provider.values()),
        "payments_by_provider": by_provider,
        "revenue_by_class_cents": revenue_class_sales(session, location_id, business_date),
    }
