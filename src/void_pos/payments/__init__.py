"""Payments: a provider seam plus built-in cash/external providers.

The open core records payments (provider + external reference) and drives the order
state machine; the Stripe Connect adapter and the platform fee lever are a commercial
module that implements ``PaymentProvider`` — they are deliberately not in this repo.
"""

from __future__ import annotations

from ..balance import balance_cents, captured_cents, order_total_cents
from .provider import (
    ChargeRequest,
    ChargeResult,
    PaymentError,
    PaymentProvider,
    RefundRequest,
    RefundResult,
)
from .providers import CashProvider, ExternalProvider
from .service import PaymentOutcome, RefundOutcome, pay_order, refund_payment

__all__ = [
    "CashProvider",
    "ChargeRequest",
    "ChargeResult",
    "ExternalProvider",
    "PaymentError",
    "PaymentOutcome",
    "PaymentProvider",
    "RefundOutcome",
    "RefundRequest",
    "RefundResult",
    "balance_cents",
    "captured_cents",
    "order_total_cents",
    "pay_order",
    "refund_payment",
]
