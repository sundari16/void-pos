from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from support import Venue, api, auth, cmd, make_engine, order_body, seed_venue
from void_pos.api.deps import get_stripe
from void_pos.config import Settings
from void_pos.errors import PaymentFailed
from void_pos.ids import new_ulid
from void_pos.main import app
from void_pos.payments.stripe import StripeHttp
from void_pos.staff import create_staff


class FakeStripe:
    """Stripe's sandbox, in memory: intents succeed once 'confirmed' by the test."""

    def __init__(self) -> None:
        self.intents: dict[str, dict[str, Any]] = {}
        self.refunds: list[dict[str, Any]] = []
        self.keys: set[str] = set()

    def create_intent(self, *, amount_cents: int, currency: str, idempotency_key: str,
                      metadata: dict[str, str]) -> dict[str, Any]:
        for intent in self.intents.values():
            if intent["key"] == idempotency_key:
                return intent
        intent_id = f"pi_{len(self.intents) + 1}"
        self.intents[intent_id] = {
            "id": intent_id, "client_secret": f"{intent_id}_secret", "amount": amount_cents,
            "currency": currency, "metadata": metadata, "status": "requires_payment_method",
            "key": idempotency_key,
        }
        return self.intents[intent_id]

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        return self.intents[intent_id]

    def refund(self, *, intent_id: str, amount_cents: int, idempotency_key: str) -> dict[str, Any]:
        self.refunds.append({"intent": intent_id, "amount": amount_cents, "key": idempotency_key})
        return {"id": f"re_{len(self.refunds)}", "status": "succeeded"}


Ctx = tuple[TestClient, Venue, Session, FakeStripe]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    stripe = FakeStripe()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            app.dependency_overrides[get_stripe] = lambda: stripe
            yield client, venue, s, stripe


def _order(c: TestClient, venue: Venue) -> str:
    return str(c.post("/orders", json=order_body(venue, seq=1), headers=auth(venue.token)).json()["id"])


def _intent(c: TestClient, venue: Venue, order_id: str, payment_id: str, **body: Any) -> Any:
    return c.post(f"/orders/{order_id}/stripe-intent",
                  json={"payment_id": payment_id, **body}, headers=auth(venue.token))


def _pay(c: TestClient, venue: Venue, order_id: str, seq: int, payment_id: str, intent: str,
         **body: Any) -> Any:
    return c.post(f"/orders/{order_id}/pay", json={
        **cmd(seq), "payment_id": payment_id, "method": "stripe",
        "stripe_payment_intent_id": intent, **body,
    }, headers=auth(venue.token))


def test_a_card_payment_is_recorded_only_once_stripe_says_it_went_through(ctx: Ctx) -> None:
    c, venue, _, stripe = ctx
    order_id, payment_id = _order(c, venue), new_ulid()
    intent = _intent(c, venue, order_id, payment_id, amount_cents=3700, tip_cents=300)
    assert intent.status_code == 200, intent.text
    pi = intent.json()["payment_intent_id"]
    assert stripe.intents[pi]["amount"] == 4000
    assert stripe.intents[pi]["metadata"]["payment_id"] == payment_id

    early = _pay(c, venue, order_id, 2, payment_id, pi, amount_cents=3700, tip_cents=300)
    assert (early.status_code, early.json()["code"]) == (402, "payment_failed")
    assert "hasn't gone through" in early.json()["detail"]

    stripe.intents[pi]["status"] = "succeeded"
    wrong = _pay(c, venue, order_id, 2, new_ulid(), pi, amount_cents=3700, tip_cents=300)
    assert wrong.status_code == 402  # someone else's intent
    paid = _pay(c, venue, order_id, 2, payment_id, pi, amount_cents=3700, tip_cents=300)
    assert paid.status_code == 200, paid.text
    assert (paid.json()["provider"], paid.json()["order_state"], paid.json()["tip_cents"]) == (
        "stripe", "paid", 300,
    )
    read = c.get(f"/orders/{order_id}", headers=auth(venue.token)).json()
    assert read["payments"][0]["external_ref"] == pi


def test_intents_are_checked_against_what_is_owing(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    order_id = _order(c, venue)
    over = _intent(c, venue, order_id, new_ulid(), amount_cents=3701)
    assert (over.status_code, over.json()["code"]) == (422, "amount_exceeds_balance")
    again = _intent(c, venue, order_id, (pid := new_ulid()), amount_cents=1000)
    assert _intent(c, venue, order_id, pid, amount_cents=1000).json()["payment_intent_id"] == (
        again.json()["payment_intent_id"]
    )


def test_a_card_payment_is_refunded_through_stripe(ctx: Ctx) -> None:
    c, venue, s, stripe = ctx
    order_id, payment_id = _order(c, venue), new_ulid()
    pi = _intent(c, venue, order_id, payment_id, amount_cents=3700).json()["payment_intent_id"]
    stripe.intents[pi]["status"] = "succeeded"
    _pay(c, venue, order_id, 2, payment_id, pi, amount_cents=3700)
    create_staff(s, location_id=venue.location_id, name="Asha", role="manager", pin="4821")
    s.commit()
    refund = c.post(f"/orders/{order_id}/refunds", json={
        **cmd(3), "refund_id": (rid := new_ulid()), "payment_id": payment_id,
        "amount_cents": 1000, "reason": "Cold", "manager_pin": "4821",
    }, headers=auth(venue.token))
    assert refund.status_code == 200, refund.text
    assert stripe.refunds == [{"intent": pi, "amount": 1000, "key": rid}]
    assert refund.json()["refunds"][0]["external_ref"] == "re_1"


def test_without_keys_cards_say_they_arent_set_up(ctx: Ctx) -> None:
    c, venue, _, _ = ctx
    app.dependency_overrides[get_stripe] = lambda: None
    r = _intent(c, venue, _order(c, venue), new_ulid(), amount_cents=100)
    assert (r.status_code, r.json()["code"]) == (503, "stripe_not_set_up")
    config = c.get("/payments/stripe", headers=auth(venue.token)).json()
    assert config == {"enabled": False, "publishable_key": None}


def test_only_sandbox_keys_are_accepted() -> None:
    with pytest.raises(ValidationError, match="sandbox"):
        Settings(_env_file=None, stripe_secret_key="sk_live_x")  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="sandbox"):
        Settings(_env_file=None, stripe_publishable_key="pk_live_x")  # type: ignore[call-arg]
    ok = Settings(_env_file=None, stripe_secret_key="sk_test_x", stripe_publishable_key="pk_test_y")  # type: ignore[call-arg]
    assert ok.stripe_enabled


def test_the_http_client_sends_the_key_and_reports_stripes_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/refunds"):
            return httpx.Response(400, json={"error": {"message": "Charge already refunded"}})
        return httpx.Response(200, json={"id": "pi_9", "client_secret": "s", "status": "x"})

    client = httpx.Client(base_url="https://api.stripe.com/v1", transport=httpx.MockTransport(handler))
    http = StripeHttp("sk_test_abc", client)
    http.create_intent(amount_cents=500, currency="AUD", idempotency_key="k1",
                       metadata={"order_id": "o"})
    assert seen[0].headers["Idempotency-Key"] == "k1"
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert b"amount=500" in seen[0].content and b"currency=aud" in seen[0].content
    with pytest.raises(PaymentFailed, match="Charge already refunded"):
        http.refund(intent_id="pi_9", amount_cents=100, idempotency_key="r1")
