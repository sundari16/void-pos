"""Card payments through Stripe (sandbox), taken on the till's screen.

The till asks for a PaymentIntent for what it's charging (``create_intent``), the
customer's card goes straight from the browser to Stripe through Stripe's Payment Element,
and then the till records the payment with the intent's id. Recording checks with Stripe
that the intent succeeded, for this order and payment and this exact amount, so a till
can't record money that didn't move. The secret key stays on the server; it is never
logged or returned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .provider import ChargeRequest, ChargeResult, PaymentError, RefundRequest, RefundResult

API = "https://api.stripe.com/v1"


class StripeApi(Protocol):
    def create_intent(
        self, *, amount_cents: int, currency: str, idempotency_key: str, metadata: dict[str, str]
    ) -> dict[str, Any]: ...

    def get_intent(self, intent_id: str) -> dict[str, Any]: ...

    def refund(self, *, intent_id: str, amount_cents: int, idempotency_key: str) -> dict[str, Any]: ...


class StripeHttp:
    """The few Stripe calls the till needs, over HTTPS with the secret key."""

    def __init__(self, secret_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(base_url=API, timeout=20)
        self._auth = (secret_key, "")

    def _call(self, method: str, path: str, data: dict[str, str] | None = None,
              idempotency_key: str | None = None) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        try:
            response = self._client.request(method, path, data=data, auth=self._auth, headers=headers)
        except httpx.HTTPError as exc:
            raise PaymentError("can't reach Stripe; check the internet and try again") from exc
        body: dict[str, Any] = response.json()
        if response.status_code >= 400:
            message = (body.get("error") or {}).get("message") or "Stripe refused the request"
            raise PaymentError(f"Stripe: {message}")
        return body

    def create_intent(
        self, *, amount_cents: int, currency: str, idempotency_key: str, metadata: dict[str, str]
    ) -> dict[str, Any]:
        data = {
            "amount": str(amount_cents),
            "currency": currency.lower(),
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
            **{f"metadata[{k}]": v for k, v in metadata.items()},
        }
        return self._call("POST", "/payment_intents", data, idempotency_key)

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        return self._call("GET", f"/payment_intents/{intent_id}")

    def refund(self, *, intent_id: str, amount_cents: int, idempotency_key: str) -> dict[str, Any]:
        data = {"payment_intent": intent_id, "amount": str(amount_cents)}
        return self._call("POST", "/refunds", data, idempotency_key)


@dataclass
class StripeProvider:
    """Records a card payment the customer made through Stripe on the till's screen."""

    api: StripeApi
    intent_id: str = ""
    payment_id: str = ""
    name: str = "stripe"

    def charge(self, request: ChargeRequest) -> ChargeResult:
        if not self.intent_id:
            raise PaymentError("the card payment wasn't completed on the till")
        intent = self.api.get_intent(self.intent_id)
        meta = intent.get("metadata") or {}
        if meta.get("order_id") != request.order_id or meta.get("payment_id") != self.payment_id:
            raise PaymentError("that card payment belongs to a different order")
        if intent.get("amount") != request.amount_cents:
            raise PaymentError("the card payment's amount doesn't match")
        if intent.get("status") != "succeeded":
            raise PaymentError(f"the card payment hasn't gone through ({intent.get('status')})")
        return ChargeResult(
            provider=self.name,
            external_ref=str(intent["id"]),
            amount_cents=request.amount_cents,
            currency=str(intent.get("currency", request.currency)).upper(),
            status="captured",
        )

    def refund(self, request: RefundRequest) -> RefundResult:
        if not request.payment_external_ref:
            raise PaymentError("that card payment has no Stripe reference")
        refund = self.api.refund(
            intent_id=request.payment_external_ref,
            amount_cents=request.amount_cents,
            idempotency_key=request.refund_id,
        )
        if refund.get("status") not in ("succeeded", "pending"):
            raise PaymentError(f"Stripe didn't refund it ({refund.get('status')})")
        return RefundResult(external_ref=str(refund["id"]))
