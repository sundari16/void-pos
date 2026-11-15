from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..events import ingest_event
from ..ids import new_ulid
from ..models import Device, Order, OrderLine, Payment
from .provider import ChargeRequest, PaymentError, PaymentProvider


def order_total_cents(session: Session, order_id: str) -> int:
    """Sum of every line (items and modifier child lines) times its quantity."""
    lines = session.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).all()
    return sum(line.unit_price_cents * line.quantity for line in lines)


def pay_order(
    session: Session,
    order_id: str,
    provider: PaymentProvider,
    *,
    amount_cents: int | None = None,
    currency: str = "AUD",
    payment_id: str | None = None,
) -> Payment:
    """Charge an order through ``provider`` and record the result as an ``order.paid``
    event (which the apply layer projects into a Payment and the order's state).

    Defaults the amount to the order total. Raises ``PaymentError`` if the charge fails.
    """
    order = session.get(Order, order_id)
    if order is None:
        raise LookupError(f"order {order_id!r} not found")

    amount = amount_cents if amount_cents is not None else order_total_cents(session, order_id)
    result = provider.charge(
        ChargeRequest(order_id=order_id, amount_cents=amount, currency=currency, idempotency_key=order_id)
    )
    if result.status == "failed":
        raise PaymentError(f"charge failed via {result.provider}")

    pid = payment_id or new_ulid()
    device = session.get(Device, order.device_id)
    ingest_event(
        session,
        {
            "event_id": new_ulid(),
            "device_id": order.device_id,
            "seq": (device.last_seq + 1) if device is not None else 1,
            "location_id": order.location_id,
            "business_date": order.business_date,
            "occurred_at": datetime.now(UTC),
            "type": "order.paid",
            "payload": {
                "order_id": order_id,
                "payment": {
                    "id": pid,
                    "provider": result.provider,
                    "external_ref": result.external_ref,
                    "amount_cents": result.amount_cents,
                    "currency": result.currency,
                    "status": result.status,
                },
            },
        },
    )
    session.flush()
    return session.get(Payment, pid)
