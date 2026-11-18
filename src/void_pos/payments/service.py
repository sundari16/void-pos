from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..commands import Command, find_replay, record
from ..errors import Conflict, InvalidRequest
from ..events import EventType
from ..events.types import TERMINAL
from ..models import Device, Order, OrderLine, Payment
from ..orders import get_order
from ..totals import total_cents
from .provider import ChargeRequest, PaymentError, PaymentProvider


@dataclass(frozen=True)
class PaymentOutcome:
    payment: Payment
    order: Order
    replayed: bool


def order_total_cents(session: Session, order_id: str) -> int:
    """Every line's price times its extended quantity (a modifier counts once per item)."""
    return total_cents(session.scalars(select(OrderLine).where(OrderLine.order_id == order_id)))


def pay_order(
    session: Session,
    device: Device,
    order_id: str,
    provider: PaymentProvider,
    command: Command,
    *,
    payment_id: str,
    amount_cents: int | None = None,
    currency: str = "AUD",
) -> PaymentOutcome:
    """Charge an order through ``provider`` and record an ``order.paid`` event, which the
    apply layer projects into a Payment and the order's state.

    Every check runs before the provider is charged: a replay returns the original
    payment, and a paid or voided order, or an amount other than the total, is refused.
    ``payment_id`` doubles as the provider's idempotency key.
    """
    # Locked until commit: a second till paying the same order waits, then sees it paid.
    order = get_order(session, order_id, location_id=device.location_id, for_update=True)
    replay = find_replay(
        session, command, device=device, event_type=EventType.ORDER_PAID, order_id=order_id
    )
    if replay is not None:
        earlier = session.get(Payment, (replay.payload.get("payment") or {}).get("id", ""))
        if earlier is None:
            raise Conflict(
                "this event_id was already used without a payment", code="event_id_reused"
            )
        return PaymentOutcome(earlier, order, replayed=True)
    if session.get(Payment, payment_id) is not None:
        raise Conflict("a payment with this id already exists", code="payment_exists")
    if order.state in TERMINAL:
        raise Conflict(
            f"this order is already {order.state}", code="order_closed", order_state=order.state
        )

    total = order_total_cents(session, order_id)
    amount = total if amount_cents is None else amount_cents
    if amount != total:
        raise InvalidRequest(
            f"the payment must cover the order total of {total} cents; "
            "split and partial payments aren't supported yet",
            code="amount_mismatch",
            total_cents=total,
        )

    result = provider.charge(
        ChargeRequest(
            order_id=order_id, amount_cents=amount, currency=currency, idempotency_key=payment_id
        )
    )
    if result.status == "failed":
        raise PaymentError(f"the charge failed via {result.provider}")

    record(
        session,
        device,
        command,
        event_type=EventType.ORDER_PAID,
        payload={
            "order_id": order_id,
            "payment": {
                "id": payment_id,
                "provider": result.provider,
                "external_ref": result.external_ref,
                "amount_cents": result.amount_cents,
                "currency": result.currency,
                "status": result.status,
            },
        },
        business_date=order.business_date,
    )
    payment = session.get(Payment, payment_id)
    assert payment is not None, "order.paid projects its payment"
    return PaymentOutcome(payment, order, replayed=False)
