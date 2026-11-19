"""Delivery-aggregator order ingestion, menu sync-out, the webhook and the CLI simulator.

Every path runs on the built-in MockAggregator, so the whole pipeline is exercised without
a partner API — exactly what the open-core seam exists to make possible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, api, auth, make_engine, seed_venue
from void_pos import cli
from void_pos.aggregators import (
    AGGREGATOR_ORDER_NUMBER_START,
    AggregatorPayloadError,
    MockAggregator,
    build_menu_snapshot,
    build_provider,
    get_aggregator_order,
    get_or_create_channel_device,
    ingest_order,
    reject_order,
    sync_menu_out,
)
from void_pos.devices import create_location
from void_pos.errors import Conflict, NotFound
from void_pos.menu import attach_modifier_group, create_modifier, create_modifier_group
from void_pos.models import AggregatorOrder, Base, Device, Order, OrderLine, Payment
from void_pos.orders import list_orders

TAMIL_DOSA = "மசாலா தோசை"
SECRET = "aggregator-webhook-secret-for-the-test-suite"


def mock_order(
    external_id: str = "MOCK-1001",
    *,
    price: int = 1850,
    qty: int = 2,
    with_modifier: bool = True,
    menu_item_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """A plain partner order payload the MockAggregator understands."""
    item: dict[str, Any] = {
        "name": "Masala Dosa",
        "quantity": qty,
        "unit_price_cents": price,
        "kitchen_name": TAMIL_DOSA,
        "category": "Mains",
        "tax_rate": 0.1,
    }
    if menu_item_id:
        item["menu_item_id"] = menu_item_id
    if with_modifier:
        item["modifiers"] = [{"name": "Extra hot", "unit_price_cents": 0}]
    return {
        "id": external_id,
        "display_id": "MCK-7Q2K",
        "fulfillment": "delivery",
        "payment_ref": "pi_mock_abc",
        "items": [item],
        **extra,
    }


# --- the mock adapter --------------------------------------------------------------


def test_mock_normalizes_an_order_with_modifiers() -> None:
    order = MockAggregator().normalize_order(mock_order())
    assert order.external_order_id == "MOCK-1001"
    assert (order.fulfillment, order.display_id, order.external_payment_ref) == (
        "delivery", "MCK-7Q2K", "pi_mock_abc",
    )
    (line,) = order.lines
    assert (line.name, line.unit_price_cents, line.quantity, line.tax_rate) == (
        "Masala Dosa", 1850, 2, 0.1,
    )
    assert [(m.name, m.unit_price_cents) for m in line.modifiers] == [("Extra hot", 0)]


def test_mock_refuses_a_malformed_order() -> None:
    agg = MockAggregator()
    with pytest.raises(AggregatorPayloadError):
        agg.normalize_order({"id": "X"})  # no items
    with pytest.raises(AggregatorPayloadError):
        agg.normalize_order({"id": "X", "items": [{"unit_price_cents": 100}]})  # no name
    with pytest.raises(AggregatorPayloadError):
        agg.normalize_order({"items": [{"name": "a", "unit_price_cents": 1}]})  # no id


def test_build_provider_ships_only_the_mock() -> None:
    assert isinstance(build_provider("mock"), MockAggregator)
    with pytest.raises(NotFound, match="commercial"):
        build_provider("uber_eats")
    with pytest.raises(NotFound, match="unknown"):
        build_provider("nope")


# --- ingestion ---------------------------------------------------------------------


def test_ingest_creates_a_paid_kitchen_bound_aggregator_order() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        agg = MockAggregator()
        outcome = ingest_order(s, agg, venue.location_id, mock_order(menu_item_id=venue.item_id))
        s.commit()

        order = outcome.order
        assert order.channel == "aggregator"
        assert order.fulfillment == "delivery"
        assert order.state == "paid"
        assert order.table_label == "MCK-7Q2K"  # the platform ref, for the kitchen/receipt
        assert order.order_number == AGGREGATOR_ORDER_NUMBER_START

        lines = list(s.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
        assert len(lines) == 2  # item + modifier
        item = next(ln for ln in lines if ln.parent_line_id is None)
        assert item.menu_item_id == venue.item_id  # linked to our item when the id is given
        assert item.name_snapshot == "Masala Dosa" and item.kitchen_name_snapshot == TAMIL_DOSA
        assert all(ln.sent_at is not None for ln in lines)  # fired to the kitchen

        payment = s.scalars(select(Payment).where(Payment.order_id == order.id)).one()
        assert payment.provider == "aggregator"
        assert payment.external_ref == "pi_mock_abc"  # the partner's ref, never the key
        assert payment.amount_cents == 3700 and order.business_date is not None

        assert agg.accepted == [("MOCK-1001", None)]  # confirmed with the partner
        assert outcome.tie.status == "accepted" and outcome.tie.order_id == order.id


def test_ingest_is_idempotent_by_partner_order_id() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        agg = MockAggregator()
        raw = mock_order()
        first = ingest_order(s, agg, venue.location_id, raw)
        s.commit()
        second = ingest_order(s, agg, venue.location_id, dict(raw))
        s.commit()

        assert second.replayed is True and second.order.id == first.order.id
        assert s.scalar(select(func.count()).select_from(Order)) == 1
        assert s.scalar(select(func.count()).select_from(AggregatorOrder)) == 1
        assert s.scalar(select(func.count()).select_from(Payment)) == 1
        assert agg.accepted == [("MOCK-1001", None)]  # the partner is confirmed once


def test_two_orders_get_distinct_numbers_on_one_channel_device() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        agg = MockAggregator()
        a = ingest_order(s, agg, venue.location_id, mock_order("A"))
        b = ingest_order(s, agg, venue.location_id, mock_order("B"))
        s.commit()
        assert a.order.device_id == b.order.device_id  # one virtual device
        assert {a.order.order_number, b.order.order_number} == {
            AGGREGATOR_ORDER_NUMBER_START, AGGREGATOR_ORDER_NUMBER_START + 1,
        }
        assert s.scalar(select(func.count()).select_from(Device)) == 2  # the till + the channel


def test_an_ingested_order_routes_and_lists_like_any_order() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        ingest_order(s, MockAggregator(), venue.location_id, mock_order())
        s.commit()
        paid = list_orders(s, venue.location_id, states=("paid",))
        assert len(paid) == 1 and paid[0].channel == "aggregator"


# --- reject ------------------------------------------------------------------------


def test_reject_declines_without_creating_an_order() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        agg = MockAggregator()
        out = reject_order(s, agg, venue.location_id, mock_order(), reason="kitchen closed")
        s.commit()
        assert out.tie.status == "rejected" and out.tie.order_id is None
        assert out.tie.reason == "kitchen closed"
        assert agg.rejected == [("MOCK-1001", "kitchen closed")]
        assert s.scalar(select(func.count()).select_from(Order)) == 0

        again = reject_order(s, agg, venue.location_id, mock_order(), reason="still closed")
        s.commit()
        assert again.replayed is True and len(agg.rejected) == 1  # idempotent


def test_rejected_cant_be_ingested_and_accepted_cant_be_rejected() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        agg = MockAggregator()
        reject_order(s, agg, venue.location_id, mock_order("A"), reason="no")
        s.commit()
        with pytest.raises(Conflict, match="rejected"):
            ingest_order(s, agg, venue.location_id, mock_order("A"))

        ingest_order(s, agg, venue.location_id, mock_order("B"))
        s.commit()
        with pytest.raises(Conflict, match="accepted"):
            reject_order(s, agg, venue.location_id, mock_order("B"), reason="too late")


def test_channel_device_is_reserved_and_reused() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        first = get_or_create_channel_device(s, venue.location_id)
        again = get_or_create_channel_device(s, venue.location_id)
        assert first.id == again.id
        assert first.order_number_start == AGGREGATOR_ORDER_NUMBER_START
        assert first.token_hash is None and first.paired_at is None  # never paired


# --- menu sync out -----------------------------------------------------------------


def test_menu_snapshot_and_sync_out() -> None:
    with Session(make_engine()) as s:
        venue = seed_venue(s)
        group = create_modifier_group(
            s, location_id=venue.location_id, name="Spice", min_select=1, max_select=1
        )
        create_modifier(s, group_id=group.id, name="Hot", price_delta_cents=0)
        attach_modifier_group(s, menu_item_id=venue.item_id, modifier_group_id=group.id)
        s.commit()

        snapshot = build_menu_snapshot(s, venue.location_id)
        assert len(snapshot.items) == 1 and snapshot.items[0].price_cents == 1850
        assert snapshot.items[0].modifier_group_ids == [group.id]
        assert len(snapshot.groups) == 1 and snapshot.groups[0].modifiers[0].name == "Hot"

        agg = MockAggregator()
        result = sync_menu_out(s, agg, venue.location_id)
        assert (result.status, result.item_count, result.provider) == ("published", 1, "mock")
        assert len(agg.published) == 1 and agg.published[0]["items"][0]["name"] == "Masala Dosa"


# --- the webhook -------------------------------------------------------------------


def _url(location_id: str, suffix: str = "orders") -> str:
    return f"/aggregators/mock/locations/{location_id}/{suffix}"


def test_webhook_ingests_with_the_shared_secret() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, aggregator_secret=SECRET) as client:
        headers = {"X-Void-Aggregator-Secret": SECRET}
        first = client.post(_url(venue.location_id), headers=headers, json=mock_order())
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["status"] == "accepted" and body["order_state"] == "paid"
        assert body["order_number"] == AGGREGATOR_ORDER_NUMBER_START and body["replayed"] is False

        second = client.post(_url(venue.location_id), headers=headers, json=mock_order())
        assert second.json()["replayed"] is True  # idempotent over HTTP too


def test_webhook_refuses_a_bad_or_missing_secret() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, aggregator_secret=SECRET) as client:
        bad = client.post(
            _url(venue.location_id), headers={"X-Void-Aggregator-Secret": "nope"}, json=mock_order()
        )
        assert bad.status_code == 403
        missing = client.post(_url(venue.location_id), json=mock_order())
        assert missing.status_code == 403
        # nothing was written
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Order)) == 0


def test_webhook_is_off_when_no_secret_is_set() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine) as client:  # no secret configured
        r = client.post(
            _url(venue.location_id), headers={"X-Void-Aggregator-Secret": "x"}, json=mock_order()
        )
        assert r.status_code == 503 and r.json()["code"] == "aggregator_disabled"


def test_webhook_reject_and_commercial_provider_and_admin_menu_sync() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
    with api(engine, aggregator_secret=SECRET) as client:
        headers = {"X-Void-Aggregator-Secret": SECRET}
        rej = client.post(
            _url(venue.location_id, "orders/reject"),
            headers=headers,
            json=mock_order("REJ-1"),
            params={"reason": "closed"},
        )
        assert rej.status_code == 200 and rej.json()["status"] == "rejected"
        assert rej.json()["order_id"] is None

        commercial = client.post(
            f"/aggregators/uber_eats/locations/{venue.location_id}/orders",
            headers=headers,
            json=mock_order("UE-1"),
        )
        assert commercial.status_code == 404 and commercial.json()["code"] == "commercial_provider"

        menu = client.post(_url(venue.location_id, "menu"), headers=auth(ADMIN_TOKEN))
        assert menu.status_code == 200 and menu.json()["item_count"] == 1


# --- the CLI simulator -------------------------------------------------------------


def test_cli_simulates_an_order_and_syncs_the_menu(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
    engine.dispose()

    order_file = tmp_path / "order.json"
    order_file.write_text(json.dumps(mock_order()), encoding="utf-8")
    ingest = ["--database-url", url, "aggregator-order", "--location", loc, "--file", str(order_file)]

    assert cli.main(ingest) == 0
    out = capsys.readouterr().out
    assert "Ingested mock order MOCK-1001" in out and "(paid)" in out
    assert cli.main(ingest) == 0
    assert "already handled" in capsys.readouterr().out  # idempotent

    assert cli.main(["--database-url", url, "aggregator-sync-menu", "--location", loc]) == 0
    assert "items to mock" in capsys.readouterr().out


def test_cli_rejects_an_order(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = f"sqlite:///{tmp_path / 'venue.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        loc = create_location(s, org_name="MCH", name="CBD").id
        s.commit()
    engine.dispose()

    order_file = tmp_path / "order.json"
    order_file.write_text(json.dumps(mock_order("REJ-9")), encoding="utf-8")
    args = [
        "--database-url", url, "aggregator-order", "--location", loc,
        "--file", str(order_file), "--reject", "--reason", "closed for the night",
    ]
    assert cli.main(args) == 0
    assert "Rejected mock order REJ-9" in capsys.readouterr().out

    engine = create_engine(url)
    with Session(engine) as s:
        tie = get_aggregator_order(s, "mock", "REJ-9")
        assert tie is not None and tie.status == "rejected"
    engine.dispose()
