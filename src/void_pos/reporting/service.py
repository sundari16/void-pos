"""Sales reporting: shift closeouts and revenue-class sales for tip-outs.

Reports are keyed on ``business_date`` (not wall-clock), so an order rung up after
midnight on the previous business day still lands in the right shift. Finer shift windows
(a time range within a day) are a later refinement.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.orm import Session, aliased

from ..errors import NotFound
from ..models import Location, Order, OrderLine, Payment, Refund
from ..orders.views import SETTLED_SALES, UNSETTLED, OrderView, list_orders, order_views


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
            Order.state.in_(SETTLED_SALES),
            OrderLine.removed_at.is_(None),
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
        "refunded": by_state.get("refunded", 0),
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

    paid, unsettled = day(SETTLED_SALES), day(UNSETTLED)
    sales = sum(v.total_cents for v in paid)
    gst = sum(v.gst_cents for v in paid)
    adjustments = [cents for v in paid for cents in v.adjustment_cents.values()]
    return {
        "paid_sales_cents": sales,
        "gst_cents": gst,
        "sales_ex_gst_cents": sales - gst,
        "discounts_cents": -sum(c for c in adjustments if c < 0),
        "surcharges_cents": sum(c for c in adjustments if c > 0),
        "open_balance_cents": sum(v.balance_cents for v in unsettled),
    }


def tips_and_refunds(session: Session, location_id: str, business_date: date) -> dict[str, Any]:
    """Tips taken and money given back on the business date. A refund's GST is its share
    of its order's GST, so net GST is what the BAS needs."""
    tips = session.execute(
        select(Payment.provider, func.sum(Payment.tip_cents))
        .where(
            Payment.location_id == location_id,
            Payment.business_date == business_date,
            Payment.status == "captured",
            Payment.tip_cents > 0,
        )
        .group_by(Payment.provider)
    ).all()
    refunds = list(
        session.scalars(
            select(Refund).where(
                Refund.location_id == location_id, Refund.business_date == business_date
            )
        )
    )
    by_provider: dict[str, int] = {}
    for refund in refunds:
        by_provider[refund.provider] = by_provider.get(refund.provider, 0) + refund.amount_cents
    today = {r.id for r in refunds}

    # A refund gives back the payment's sale part first, then its tip: walk every refund of
    # each payment in order, and keep the parts of today's.
    payment_ids = {r.payment_id for r in refunds}
    payments = {p.id: p for p in session.scalars(select(Payment).where(Payment.id.in_(payment_ids)))}
    sale_left = {p.id: p.amount_cents for p in payments.values()}
    tip_left = {p.id: p.tip_cents for p in payments.values()}
    sale_back: dict[str, int] = {}  # order id -> sale refunded today
    tips_back = 0
    for refund in session.scalars(
        select(Refund).where(Refund.payment_id.in_(payment_ids)).order_by(Refund.id)
    ):
        sale = min(refund.amount_cents, sale_left[refund.payment_id])
        tip = min(refund.amount_cents - sale, tip_left[refund.payment_id])
        sale_left[refund.payment_id] -= sale
        tip_left[refund.payment_id] -= tip
        if refund.id in today:
            sale_back[refund.order_id] = sale_back.get(refund.order_id, 0) + sale
            tips_back += tip

    views = {
        v.order.id: v
        for v in order_views(
            session, list(session.scalars(select(Order).where(Order.id.in_(sale_back))))
        )
    }
    refund_gst = 0
    for order_id, sale in sale_back.items():
        view = views[order_id]
        if view.total_cents > 0:
            share = Decimal(min(sale, view.total_cents)) * view.gst_cents / view.total_cents
            refund_gst += int(share.quantize(Decimal(1), ROUND_HALF_UP))
    tips_total = sum(int(t or 0) for _, t in tips)
    return {
        "tips_cents": tips_total,
        "tips_by_provider": {provider: int(t or 0) for provider, t in tips},
        "refunds_cents": sum(by_provider.values()),
        "refunds_by_provider": by_provider,
        "refund_sales_cents": sum(sale_back.values()),
        "refund_tips_cents": tips_back,
        "refund_gst_cents": refund_gst,
        "net_tips_cents": tips_total - tips_back,
    }


def shift_closeout(session: Session, location_id: str, business_date: date) -> dict[str, Any]:
    """End-of-shift reconciliation for a location and business date."""
    if session.get(Location, location_id) is None:
        raise NotFound("location not found")

    by_provider = payments_by_provider(session, location_id, business_date)
    sales = sales_and_gst(session, location_id, business_date)
    money_back = tips_and_refunds(session, location_id, business_date)
    return {
        "location_id": location_id,
        "business_date": business_date.isoformat(),
        "generated_at": datetime.now(UTC).isoformat(),
        "orders": _order_counts(session, location_id, business_date),
        "gross_sales_cents": sum(by_provider.values()),
        **sales,
        **money_back,
        "net_sales_cents": sales["paid_sales_cents"] - money_back["refund_sales_cents"],
        "net_gst_cents": sales["gst_cents"] - money_back["refund_gst_cents"],
        "payments_by_provider": by_provider,
        "revenue_by_class_cents": revenue_class_sales(session, location_id, business_date),
    }
