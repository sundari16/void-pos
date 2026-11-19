from __future__ import annotations

from dataclasses import dataclass

from .provider import ChargeRequest, ChargeResult, PaymentError, RefundRequest, RefundResult


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

    def refund(self, request: RefundRequest) -> RefundResult:
        return RefundResult(external_ref=None)  # counted out of the till


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

    def refund(self, request: RefundRequest) -> RefundResult:
        """Done on the venue's terminal; the cashier types its refund reference."""
        if not self.external_ref:
            raise PaymentError("type the terminal's refund reference")
        return RefundResult(external_ref=self.external_ref)


@dataclass
class LoyaltyProvider:
    """Loyalty points redeemed at checkout, recorded as a non-cash tender toward the order.
    No money moves: the points are deducted from the ledger separately; this records the
    value they paid. ``external_ref`` is the ledger entry that debited the points."""

    external_ref: str
    name: str = "loyalty"

    def charge(self, request: ChargeRequest) -> ChargeResult:
        return ChargeResult(
            provider=self.name,
            external_ref=self.external_ref,
            amount_cents=request.amount_cents,
            currency=request.currency,
            status="captured",
        )

    def refund(self, request: RefundRequest) -> RefundResult:
        # Reversing a redemption re-credits points in the ledger, not here.
        return RefundResult(external_ref=request.payment_external_ref)
