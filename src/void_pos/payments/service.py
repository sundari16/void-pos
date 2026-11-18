from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from ..balance import balance_cents
from ..commands import Command, find_replay, record
from ..errors import Conflict, InvalidRequest
from ..events import EventType
from ..events.types import TERMINAL
from ..models import Device, Order, Payment
from ..orders import get_order
from .provider import ChargeRequest, PaymentError, PaymentProvider


@dataclass(frozen=True)
class PaymentOutcome:
    payment: Payment
    order: Order
    balance_cents: int  # still owing after this payment
    replayed: bool


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
    """Charge part or all of what is owing on an order through ``provider`` and record an
    ``order.paid`` event, which the apply layer projects into a Payment. The order turns
    paid once its payments cover the total; until then it stays open (or sent) for the
    next payment, as when a bill is split.

    Every check runs before the provider is charged: a replay returns the original
    payment, and a paid or voided order, or an amount that is zero or more than is owing,
    is refused. The amount defaults to the whole balance. ``payment_id`` doubles as the
    provider's idempotency key.
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
        return PaymentOutcome(earlier, order, balance_cents(session, order_id), replayed=True)
    if session.get(Payment, payment_id) is not None:
        raise Conflict("a payment with this id already exists", code="payment_exists")
    if order.state in TERMINAL:
        raise Conflict(
            f"this order is already {order.state}", code="order_closed", order_state=order.state
        )

    owing = balance_cents(session, order_id)
    amount = owing if amount_cents is None else amount_cents
    if amount > owing:
        raise InvalidRequest(
            f"the payment of {amount} cents is more than the {owing} cents owing",
            code="amount_exceeds_balance",
            balance_cents=owing,
        )
    if amount <= 0 and owing > 0:
        raise InvalidRequest(
            "a payment needs an amount above zero", code="invalid_amount", balance_cents=owing
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
    return PaymentOutcome(payment, order, balance_cents(session, order_id), replayed=False)
