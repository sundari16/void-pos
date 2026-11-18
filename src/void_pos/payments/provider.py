from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..errors import PaymentFailed


class PaymentError(PaymentFailed):
    """A charge was attempted but did not succeed."""


@dataclass(frozen=True)
class ChargeRequest:
    order_id: str
    amount_cents: int
    currency: str = "AUD"
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ChargeResult:
    provider: str
    external_ref: str | None
    amount_cents: int
    currency: str
    status: str  # captured | pending | failed


@dataclass(frozen=True)
class RefundRequest:
    refund_id: str  # also the provider's idempotency key
    payment_external_ref: str | None
    amount_cents: int
    currency: str = "AUD"


@dataclass(frozen=True)
class RefundResult:
    external_ref: str | None


class PaymentProvider(Protocol):
    """A payment backend. Built-in providers (cash, external) live in the open core;
    the Stripe Connect adapter implements this same protocol in the commercial layer."""

    name: str

    def charge(self, request: ChargeRequest) -> ChargeResult: ...

    def refund(self, request: RefundRequest) -> RefundResult: ...
