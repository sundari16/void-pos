"""Card payments through Stripe (sandbox), taken on the till's screen.

The till asks for a PaymentIntent for what it's charging (``create_intent``, manual
capture), and the customer's card goes straight from the browser to Stripe through
Stripe's Payment Element, which only *authorises* it. Then the till records the payment:
the server checks the order can take it, checks with Stripe that the intent is for this
order, payment and exact amount, and only then *captures* it (idempotently) and records
it. So money moves only when it's recorded: an order that can't take the payment releases
the hold instead, and a retry after a dropped connection or a Stripe outage captures the
same intent once. The secret key stays on the server; it is never logged or returned.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..errors import ServiceUnavailable
from .provider import ChargeRequest, ChargeResult, PaymentError, RefundRequest, RefundResult

API = "https://api.stripe.com/v1"
log = logging.getLogger(__name__)


class StripeApi(Protocol):
    def create_intent(
        self, *, amount_cents: int, currency: str, idempotency_key: str, metadata: dict[str, str]
    ) -> dict[str, Any]: ...

    def get_intent(self, intent_id: str) -> dict[str, Any]: ...

    def capture(self, intent_id: str, *, idempotency_key: str) -> dict[str, Any]: ...

    def cancel(self, intent_id: str) -> dict[str, Any]: ...

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
            # Nothing is known to have happened: a 503 keeps the till's request (and its
            # idempotency keys) for a safe retry.
            raise ServiceUnavailable(
                "can't reach Stripe; check the internet and try again", code="stripe_unavailable"
            ) from exc
        if response.status_code >= 500:
            raise ServiceUnavailable("Stripe is having trouble; try again", code="stripe_unavailable")
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
            "capture_method": "manual",  # authorise on the till, capture when recorded
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
            **{f"metadata[{k}]": v for k, v in metadata.items()},
        }
        return self._call("POST", "/payment_intents", data, idempotency_key)

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        return self._call("GET", f"/payment_intents/{intent_id}")

    def capture(self, intent_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._call("POST", f"/payment_intents/{intent_id}/capture", {}, idempotency_key)

    def cancel(self, intent_id: str) -> dict[str, Any]:
        return self._call("POST", f"/payment_intents/{intent_id}/cancel", {})

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
        status = intent.get("status")
        if status == "requires_capture":
            intent = self.api.capture(self.intent_id, idempotency_key=f"capture-{self.payment_id}")
            status = intent.get("status")
        if status == "processing":
            raise ServiceUnavailable(
                "the card payment is still processing; try again", code="stripe_processing"
            )
        if status != "succeeded":
            raise PaymentError(f"the card payment hasn't gone through ({status})")
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


# A refusal of the payment itself (not a hiccup): the card's hold is released.
RELEASE_ON = frozenset(
    {"order_closed", "order_voided", "amount_exceeds_balance", "invalid_amount", "not_found"}
)


def release(api: StripeApi, intent_id: str) -> None:
    """Best effort: cancel an authorised intent the till won't record."""
    try:
        api.cancel(intent_id)
    except Exception:  # noqa: BLE001 - the hold lapses on its own if this fails
        log.warning("couldn't release Stripe intent %s; its hold will lapse", intent_id)
