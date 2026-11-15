from __future__ import annotations

from dataclasses import dataclass

from .provider import ChargeRequest, ChargeResult


class CashProvider:
    """Cash taken at the counter — recorded, no external processor."""

    name = "cash"

    def charge(self, request: ChargeRequest) -> ChargeResult:
        return ChargeResult(
            provider=self.name,
            external_ref=None,
            amount_cents=request.amount_cents,
            currency=request.currency,
            status="captured",
        )


@dataclass
class ExternalProvider:
    """Record a payment captured by an external processor — the seam the commercial
    Stripe Connect adapter plugs into. It supplies the processor's reference; this core
    never holds processor credentials and never keys on the external id."""

    external_ref: str
    name: str = "external"

    def charge(self, request: ChargeRequest) -> ChargeResult:
        return ChargeResult(
            provider=self.name,
            external_ref=self.external_ref,
            amount_cents=request.amount_cents,
            currency=request.currency,
            status="captured",
        )
