from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from support import TILL_TOKEN, Venue, api, auth, make_engine, seed_venue
from void_pos.api.deps import get_settings, get_stripe
from void_pos.config import Settings
from void_pos.ids import new_ulid
from void_pos.main import app
from void_pos.models import MenuItem, Order


class FakeStripe:
    """A tiny in-memory Stripe, like tests/test_stripe.py's."""

    def __init__(self) -> None:
        self.intents: dict[str, dict[str, Any]] = {}
        self.captures: list[str] = []
        self.cancelled: list[str] = []

    def create_intent(self, *, amount_cents, currency, idempotency_key, metadata):  # type: ignore[no-untyped-def]
        for intent in self.intents.values():
            if intent["key"] == idempotency_key:
                return intent
        iid = f"pi_{len(self.intents) + 1}"
        self.intents[iid] = {
            "id": iid, "client_secret": f"{iid}_secret", "amount": amount_cents,
            "currency": currency, "metadata": metadata, "status": "requires_payment_method",
            "key": idempotency_key,
        }
        return self.intents[iid]

    def get_intent(self, intent_id): return self.intents[intent_id]  # type: ignore[no-untyped-def]

    def capture(self, intent_id, *, idempotency_key):  # type: ignore[no-untyped-def]
        intent = self.intents[intent_id]
        if intent["status"] == "requires_capture":
            self.captures.append(idempotency_key)
            intent["status"] = "succeeded"
        return intent

    def cancel(self, intent_id):  # type: ignore[no-untyped-def]
        self.cancelled.append(intent_id)
        self.intents[intent_id]["status"] = "canceled"
        return self.intents[intent_id]


Ctx = tuple[TestClient, Venue, Session, FakeStripe, Engine]


@pytest.fixture
def ctx() -> Iterator[Ctx]:
    engine = make_engine()
    stripe = FakeStripe()
    with Session(engine) as s:
        venue = seed_venue(s)
        with api(engine) as client:
            yield client, venue, s, stripe, engine


def settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, admin_token="admin-token-for-the-test-suite", **kw)


def with_settings(**kw: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: settings(**kw)


def with_stripe(stripe: FakeStripe) -> None:
    app.dependency_overrides[get_stripe] = lambda: stripe


def item_line(venue: Venue, *, price: int = 1850, qty: int = 1) -> dict[str, Any]:
    return {
        "id": new_ulid(), "menu_item_id": venue.item_id, "quantity": qty,
        "name_snapshot": "Masala Dosa", "unit_price_cents": price, "category_snapshot": "Mains",
    }


def place(client: TestClient, venue: Venue, **over: Any) -> Any:
    body = {
        "order_id": over.pop("order_id", new_ulid()),
        "customer": over.pop("customer", {"name": "Ada", "phone": "+61400000001"}),
        "fulfillment": over.pop("fulfillment", "pickup"),
        "lines": over.pop("lines", [item_line(venue)]),
        **over,
    }
    return client.post(f"/online/locations/{venue.location_id}/orders", json=body)


# --- storefront + ordering ---------------------------------------------------------


def test_storefront_exposes_the_menu_and_capabilities(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    r = client.get(f"/online/locations/{venue.location_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location_name"] == "Brisbane CBD"
    assert {i["name"] for i in body["menu"]["items"]} == {"Masala Dosa"}
    assert body["online_pay_enabled"] is False  # no Stripe keys in the test settings
    assert body["loyalty"]["enabled"] is False


def test_a_web_order_lands_in_the_same_pipeline(ctx: Ctx) -> None:
    client, venue, s, _, _ = ctx
    r = place(client, venue)
    assert r.status_code == 200, r.text
    order_id = r.json()["order"]["id"]
    stored = s.get(Order, order_id)
    assert stored is not None and stored.channel == "web" and stored.state == "open"
    assert stored.order_number >= 2_000_000_000  # the reserved web band
    assert stored.customer_id == r.json()["customer_id"]
    # The till sees it among the location's open orders — one pipeline, not a side channel.
    till = client.get(f"/locations/{venue.location_id}/orders", headers=auth(TILL_TOKEN))
    assert order_id in {o["id"] for o in till.json()}


def test_pay_at_pickup_leaves_the_balance_owing(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    body = place(client, venue).json()
    assert body["order"]["balance_cents"] == 1850 and body["order"]["state"] == "open"
    assert body["order"]["payments"] == []


def test_delivery_needs_an_address(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    missing = place(client, venue, fulfillment="delivery")
    assert (missing.status_code, missing.json()["code"]) == (422, "delivery_address_required")
    ok = place(client, venue, fulfillment="delivery", delivery_address="1 Edward St, Brisbane")
    assert ok.status_code == 200, ok.text
    read = client.get(f"/online/orders/{ok.json()['order']['id']}").json()
    assert read["delivery_address"] == "1 Edward St, Brisbane"


def test_an_item_not_on_sale_is_refused(ctx: Ctx) -> None:
    client, venue, s, _, _ = ctx
    menu_item = s.get(MenuItem, venue.item_id)
    assert menu_item is not None
    menu_item.is_available = False
    s.commit()
    r = place(client, venue)
    assert (r.status_code, r.json()["code"]) == (409, "item_unavailable")


def test_placing_is_idempotent_on_the_order_id(ctx: Ctx) -> None:
    client, venue, s, _, _ = ctx
    oid = new_ulid()
    first = place(client, venue, order_id=oid)
    second = place(client, venue, order_id=oid)
    assert first.json()["order"]["replayed"] is False
    assert second.json()["order"]["replayed"] is True
    assert second.json()["order"]["id"] == oid
    count = sum(1 for _ in s.query(Order).filter(Order.id == oid))
    assert count == 1  # placed once, not twice


def test_a_returning_customer_accrues_history(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    customer_id = place(
        client, venue, customer={"name": "Ada", "phone": "+61 400 111 222"}
    ).json()["customer_id"]
    again = place(client, venue, customer={"name": "Ada", "phone": "+61-400-111-222"})
    assert again.json()["customer_id"] == customer_id
    history = client.get(f"/online/customers/{customer_id}/orders").json()
    assert len(history["orders"]) == 2


def test_online_can_be_turned_off(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    with_settings(online_ordering_enabled=False)
    r = client.get(f"/online/locations/{venue.location_id}")
    assert r.status_code == 404


# --- loyalty -----------------------------------------------------------------------


def test_points_are_earned_on_a_web_order(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    with_settings(loyalty_enabled=True)
    body = place(client, venue).json()  # $18.50
    assert body["points_earned"] == 18 and body["loyalty_balance"] == 18
    customer_id = body["customer_id"]
    bal = client.get(f"/online/customers/{customer_id}/loyalty").json()
    assert bal == {
        "customer_id": customer_id, "enabled": True, "balance": 18,
        "earn_points_per_dollar": 1, "redeem_cents_per_point": 1,
    }


def test_points_are_redeemed_at_checkout(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    with_settings(loyalty_enabled=True)
    cust = {"name": "Ada", "phone": "+61400111222"}
    place(client, venue, customer=cust)  # earns 18
    second = place(client, venue, customer=cust, redeem_points=18).json()
    assert second["redeemed_points"] == 18 and second["redeemed_cents"] == 18
    assert second["order"]["balance_cents"] == 1832  # 1850 - 18 redeemed
    loyalty_payments = [p for p in second["order"]["payments"] if p["provider"] == "loyalty"]
    assert loyalty_payments and loyalty_payments[0]["amount_cents"] == 18
    # Spent 18 points, earned 18 on the $18.32 paid -> back to 18.
    assert second["points_earned"] == 18 and second["loyalty_balance"] == 18


def test_redemption_is_capped_by_the_balance(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    with_settings(loyalty_enabled=True)
    cust = {"name": "Ada", "phone": "+61400111222"}
    place(client, venue, customer=cust)  # earns 18
    greedy = place(client, venue, customer=cust, redeem_points=10_000).json()
    assert greedy["redeemed_points"] == 18  # clamped to the balance


def test_loyalty_is_ignored_when_disabled(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    body = place(client, venue, redeem_points=50).json()
    assert body["points_earned"] == 0 and body["redeemed_points"] == 0


# --- paying online by card ---------------------------------------------------------


def _intent(client: TestClient, order_id: str, payment_id: str, **body: Any) -> Any:
    return client.post(
        f"/online/orders/{order_id}/stripe-intent", json={"payment_id": payment_id, **body}
    )


def _pay(client: TestClient, order_id: str, payment_id: str, pi: str, **body: Any) -> Any:
    return client.post(
        f"/online/orders/{order_id}/pay",
        json={"payment_id": payment_id, "stripe_payment_intent_id": pi, **body},
    )


def test_a_customer_pays_online_by_card(ctx: Ctx) -> None:
    client, venue, _, stripe, _ = ctx
    with_stripe(stripe)
    order_id = place(client, venue).json()["order"]["id"]
    payment_id = new_ulid()
    intent = _intent(client, order_id, payment_id)
    assert intent.status_code == 200, intent.text
    pi = intent.json()["payment_intent_id"]
    assert stripe.intents[pi]["amount"] == 1850  # the whole balance
    stripe.intents[pi]["status"] = "requires_capture"  # the card is authorised
    paid = _pay(client, order_id, payment_id, pi)
    assert paid.status_code == 200, paid.text
    assert (paid.json()["order_state"], paid.json()["provider"]) == ("paid", "stripe")
    assert stripe.captures == [f"capture-{payment_id}"]


def test_redeem_then_pay_the_rest_on_a_card(ctx: Ctx) -> None:
    client, venue, _, stripe, _ = ctx
    with_settings(loyalty_enabled=True)
    with_stripe(stripe)
    cust = {"name": "Ada", "phone": "+61400111222"}
    place(client, venue, customer=cust)  # earns 18
    order_id = place(client, venue, customer=cust, redeem_points=18).json()["order"]["id"]
    payment_id = new_ulid()
    pi = _intent(client, order_id, payment_id).json()["payment_intent_id"]
    assert stripe.intents[pi]["amount"] == 1832  # the balance left after redemption
    stripe.intents[pi]["status"] = "requires_capture"
    paid = _pay(client, order_id, payment_id, pi)
    assert paid.json()["order_state"] == "paid" and paid.json()["balance_cents"] == 0


def test_card_payment_needs_stripe_configured(ctx: Ctx) -> None:
    client, venue, _, _, _ = ctx
    order_id = place(client, venue).json()["order"]["id"]
    r = _intent(client, order_id, new_ulid())
    assert (r.status_code, r.json()["code"]) == (503, "stripe_not_set_up")
