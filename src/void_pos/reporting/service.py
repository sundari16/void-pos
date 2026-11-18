"""Sales reporting: shift closeouts and revenue-class sales for tip-outs.

Reports are keyed on ``business_date`` (not wall-clock), so an order rung up after
midnight on the previous business day still lands in the right shift. Finer shift windows
(a time range within a day) are a later refinement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.orm import Session, aliased

from ..errors import NotFound
from ..models import Location, Order, OrderLine, Payment
from ..orders.views import UNSETTLED, OrderView, list_orders, order_views


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
    basis for tip-outs. A modifier with no category of its own counts in its item's class,
    once per unit of the item. Lines with no category fall under ``uncategorized``."""
    item = aliased(OrderLine)
    category = func.coalesce(OrderLine.category_snapshot, item.category_snapshot)
    # 64-bit before multiplying: price x modifier qty x item qty overflows a 32-bit integer.
    quantity = cast(OrderLine.quantity, BigInteger) * func.coalesce(item.quantity, 1)
    rows = session.execute(
        select(category, func.sum(cast(OrderLine.unit_price_cents, BigInteger) * quantity))
        .join(Order, Order.id == OrderLine.order_id)
        .outerjoin(item, item.id == OrderLine.parent_line_id)
        .where(
            Order.location_id == location_id,
            Order.business_date == business_date,
            Order.state == "paid",
        )
        .group_by(category)
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


def sales_and_gst(session: Session, location_id: str, business_date: date) -> dict[str, int]:
    """Paid orders' sales and the GST inside them (prices include GST), with GST rounded
    per order exactly as each receipt shows it, plus what is still owing on the day's
    unsettled orders."""
    def day(states: tuple[str, ...]) -> list[OrderView]:
        orders = list_orders(
            session, location_id, states=states, business_date=business_date, limit=None
        )
        return order_views(session, orders)

    paid, unsettled = day(("paid",)), day(UNSETTLED)
    sales = sum(v.total_cents for v in paid)
    gst = sum(v.gst_cents for v in paid)
    return {
        "paid_sales_cents": sales,
        "gst_cents": gst,
        "sales_ex_gst_cents": sales - gst,
        "open_balance_cents": sum(v.balance_cents for v in unsettled),
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
        **sales_and_gst(session, location_id, business_date),
        "payments_by_provider": by_provider,
        "revenue_by_class_cents": revenue_class_sales(session, location_id, business_date),
    }
