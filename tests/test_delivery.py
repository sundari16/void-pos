"""On-demand delivery dispatch: the provider seam, the mock courier, the dispatch /
status / cancel service, the HTTP surface and the CLI."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from support import ADMIN_TOKEN, TILL_TOKEN, auth, make_engine, seed_venue
from void_pos.cli import build_parser, run
from void_pos.config import Settings
from void_pos.delivery import (
    DeliveryError,
    DeliveryParty,
    DeliveryRequest,
    MockCourier,
    build_provider,
    cancel_delivery,
    delivery_for_order,
    dispatch_delivery,
    get_delivery,
    poll_delivery,
    update_delivery_status,
)
from void_pos.delivery.states import can_transition
from void_pos.delivery.uber_direct import UberDirectCourier
from void_pos.errors import Conflict, InvalidRequest, NotFound, ServiceUnavailable
from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import Delivery, Location, Order

TODAY = date(2026, 11, 20)
T0 = datetime(2026, 11, 20, 10, 0, tzinfo=UTC)

DROPOFF = DeliveryParty(
    name="Priya",
    phone="+61400000000",
    address="12 Example St, Brisbane",
    notes="Buzzer 4",
)


def _session() -> Session:
    return Session(make_engine())


def _place_order(
    s: Session,
    *,
    channel: str = "pos",
    fulfillment: str = "delivery",
    with_address: bool = True,
    seq: int = 1,
    order_number: int = 1000,
) -> tuple[str, str]:
    """Seed a venue (optionally with a pickup address) and place one order. Returns
    ``(location_id, order_id)``."""
    venue = seed_venue(s)
    if with_address:
        location = s.get(Location, venue.location_id)
        assert location is not None
        location.address = "1 Curry Lane, Brisbane"
        location.phone = "+61730000000"
        s.commit()
    order_id = new_ulid()
    ingest_event(
        s,
        {
            "event_id": new_ulid(),
            "device_id": venue.device_id,
            "seq": seq,
            "location_id": venue.location_id,
            "business_date": TODAY,
            "occurred_at": T0,
            "type": "order.placed",
            "payload": {
                "order_id": order_id,
                "order_number": order_number,
                "channel": channel,
                "fulfillment": fulfillment,
                "lines": [
                    {
                        "id": new_ulid(),
                        "menu_item_id": venue.item_id,
                        "quantity": 1,
                        "name_snapshot": "Masala Dosa",
                        "unit_price_cents": 1850,
                    }
                ],
            },
        },
    )
    s.commit()
    return venue.location_id, order_id


# --- the state machine --------------------------------------------------------------


def test_state_machine_allows_forward_and_cancel() -> None:
    assert can_transition("requested", "accepted")
    assert can_transition("requested", "picked_up")  # a provider may skip steps
    assert can_transition("accepted", "picked_up")
    assert can_transition("picked_up", "delivered")
    assert can_transition("requested", "canceled")
    assert not can_transition("picked_up", "canceled")  # too late once collected
    assert not can_transition("delivered", "picked_up")  # no going back
    assert not can_transition("canceled", "accepted")


# --- the provider seam / registry ---------------------------------------------------


def test_build_provider_mock_ok_commercial_and_unknown_refused() -> None:
    assert build_provider("mock").name == "mock"
    with pytest.raises(NotFound) as commercial:
        build_provider("uber_direct")
    assert commercial.value.code == "commercial_provider"
    with pytest.raises(NotFound) as unknown:
        build_provider("teleporter")
    assert unknown.value.code == "unknown_provider"


def test_uber_direct_stub_is_a_clean_seam_that_never_calls_out() -> None:
    courier = UberDirectCourier()
    assert courier.name == "uber_direct"
    req = DeliveryRequest(
        delivery_id=new_ulid(), order_reference="1", pickup=DROPOFF, dropoff=DROPOFF
    )
    for call in (
        lambda: courier.request_delivery(req),
        lambda: courier.get_status("x"),
        lambda: courier.cancel("x"),
    ):
        with pytest.raises(DeliveryError) as exc:
            call()
        assert exc.value.code == "commercial_provider"


def test_mock_courier_is_idempotent_on_delivery_id() -> None:
    courier = MockCourier()
    req = DeliveryRequest(
        delivery_id=new_ulid(), order_reference="1000", pickup=DROPOFF, dropoff=DROPOFF
    )
    first = courier.request_delivery(req)
    again = courier.request_delivery(req)
    assert first.external_ref == again.external_ref
    assert len(courier.jobs) == 1


# --- dispatch -----------------------------------------------------------------------


def test_dispatch_creates_a_tracked_delivery() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()

    outcome = dispatch_delivery(
        s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF
    )
    s.commit()

    d = outcome.delivery
    assert outcome.replayed is False
    assert d.id == delivery_id
    assert d.provider == "mock"
    assert d.external_ref is not None and d.external_ref != d.id  # never keyed on the courier id
    assert d.status == "requested"
    assert d.fee_cents == 799
    assert d.currency == "AUD"
    assert d.tracking_url is not None
    assert d.dropoff_name == "Priya" and d.dropoff_notes == "Buzzer 4"
    assert d.business_date == TODAY
    assert d.requested_at is not None
    assert courier.calls == [("request_delivery", delivery_id)]


def test_dispatch_is_idempotent_on_delivery_id() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()

    first = dispatch_delivery(
        s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF
    )
    s.commit()
    again = dispatch_delivery(
        s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF
    )
    s.commit()

    assert again.replayed is True
    assert again.delivery.id == first.delivery.id
    assert s.query(Delivery).count() == 1
    # The replay returns the stored job without asking the courier a second time.
    assert courier.calls == [("request_delivery", delivery_id)]


def test_dispatch_reusing_a_delivery_id_for_another_order_conflicts() -> None:
    s = _session()
    location_id, order_a = _place_order(s)
    # a second delivery order on the same till
    order_b = new_ulid()
    venue_device = s.query(Order).filter_by(id=order_a).one().device_id
    ingest_event(
        s,
        {
            "event_id": new_ulid(),
            "device_id": venue_device,
            "seq": 2,
            "location_id": location_id,
            "business_date": TODAY,
            "occurred_at": T0,
            "type": "order.placed",
            "payload": {
                "order_id": order_b,
                "order_number": 1001,
                "channel": "pos",
                "fulfillment": "delivery",
                "lines": [
                    {
                        "id": new_ulid(),
                        "quantity": 1,
                        "name_snapshot": "Dosa",
                        "unit_price_cents": 1850,
                    }
                ],
            },
        },
    )
    s.commit()
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_a, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    with pytest.raises(Conflict) as exc:
        dispatch_delivery(
            s, location_id, order_b, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF
        )
    assert exc.value.code == "delivery_id_reused"


def test_dispatch_refuses_a_second_active_courier_for_an_order() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF)
    s.commit()
    with pytest.raises(Conflict) as exc:
        dispatch_delivery(
            s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
        )
    assert exc.value.code == "delivery_exists"


def test_dispatch_after_the_first_is_canceled_is_allowed() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    first = dispatch_delivery(
        s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
    )
    s.commit()
    cancel_delivery(s, first.delivery.id, MockCourier())
    s.commit()
    # a fresh courier can be sent once the first is called off
    second = dispatch_delivery(
        s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
    )
    s.commit()
    assert second.replayed is False
    assert second.delivery.status == "requested"


def test_dispatch_refuses_a_non_delivery_order() -> None:
    s = _session()
    location_id, order_id = _place_order(s, fulfillment="dine_in")
    with pytest.raises(InvalidRequest) as exc:
        dispatch_delivery(
            s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
        )
    assert exc.value.code == "not_a_delivery_order"


def test_dispatch_refuses_an_aggregator_order() -> None:
    s = _session()
    location_id, order_id = _place_order(s, channel="aggregator")
    with pytest.raises(InvalidRequest) as exc:
        dispatch_delivery(
            s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
        )
    assert exc.value.code == "not_own_channel"


def test_dispatch_needs_a_venue_pickup_address() -> None:
    s = _session()
    location_id, order_id = _place_order(s, with_address=False)
    with pytest.raises(InvalidRequest) as exc:
        dispatch_delivery(
            s, location_id, order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
        )
    assert exc.value.code == "venue_address_missing"


def test_dispatch_scoped_to_the_wrong_location_is_not_found() -> None:
    s = _session()
    _location_id, order_id = _place_order(s)
    with pytest.raises(NotFound):
        dispatch_delivery(
            s, new_ulid(), order_id, MockCourier(), delivery_id=new_ulid(), dropoff=DROPOFF
        )


# --- status updates -----------------------------------------------------------------


def test_status_moves_through_the_lifecycle_with_stamps() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()

    update_delivery_status(s, delivery_id, status="accepted", courier_name="Sam")
    update_delivery_status(s, delivery_id, status="picked_up")
    out = update_delivery_status(s, delivery_id, status="delivered")
    s.commit()

    d = out.delivery
    assert d.status == "delivered"
    assert d.courier_name == "Sam"
    assert d.picked_up_at is not None
    assert d.delivered_at is not None


def test_repeating_the_current_status_is_a_noop() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    update_delivery_status(s, delivery_id, status="accepted")
    out = update_delivery_status(s, delivery_id, status="accepted", courier_name="Sam")
    assert out.replayed is True
    assert out.delivery.courier_name == "Sam"  # metadata still folds in


def test_a_backwards_status_is_refused() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    update_delivery_status(s, delivery_id, status="delivered")
    with pytest.raises(Conflict) as exc:
        update_delivery_status(s, delivery_id, status="picked_up")
    assert exc.value.code == "bad_delivery_transition"


def test_unknown_status_is_rejected() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    with pytest.raises(InvalidRequest) as exc:
        update_delivery_status(s, delivery_id, status="teleported")
    assert exc.value.code == "unknown_status"


# --- poll + cancel ------------------------------------------------------------------


def test_poll_folds_in_the_platforms_status() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()
    out = dispatch_delivery(
        s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF
    )
    s.commit()
    assert out.delivery.external_ref is not None
    courier.advance(out.delivery.external_ref, "picked_up", courier_name="Dana")

    polled = poll_delivery(s, delivery_id, courier)
    s.commit()
    assert polled.delivery.status == "picked_up"
    assert polled.delivery.courier_name == "Dana"


def test_cancel_before_pickup_calls_the_provider() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()

    out = cancel_delivery(s, delivery_id, courier, reason="customer called off")
    s.commit()
    assert out.delivery.status == "canceled"
    assert out.delivery.canceled_at is not None
    assert out.delivery.cancel_reason == "customer called off"
    assert ("cancel", out.delivery.external_ref) in courier.calls


def test_cancel_is_idempotent() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    cancel_delivery(s, delivery_id, courier)
    s.commit()
    again = cancel_delivery(s, delivery_id, courier)
    assert again.replayed is True


def test_cancel_after_pickup_is_too_late() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    courier = MockCourier()
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, courier, delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    update_delivery_status(s, delivery_id, status="picked_up")
    with pytest.raises(Conflict) as exc:
        cancel_delivery(s, delivery_id, courier)
    assert exc.value.code == "too_late_to_cancel"


def test_get_and_lookup_scope_to_the_location() -> None:
    s = _session()
    location_id, order_id = _place_order(s)
    delivery_id = new_ulid()
    dispatch_delivery(s, location_id, order_id, MockCourier(), delivery_id=delivery_id, dropoff=DROPOFF)
    s.commit()
    assert get_delivery(s, delivery_id, location_id=location_id).id == delivery_id
    assert delivery_for_order(s, order_id).id == delivery_id
    with pytest.raises(NotFound):
        get_delivery(s, delivery_id, location_id=new_ulid())


def test_provider_helper_503_when_disabled() -> None:
    from void_pos.api.delivery_routes import _provider

    with pytest.raises(ServiceUnavailable) as exc:
        _provider(Settings(_env_file=None, delivery_enabled=False))  # type: ignore[call-arg]
    assert exc.value.code == "delivery_disabled"


# --- HTTP surface -------------------------------------------------------------------


def _place_delivery_order_over_http(client, venue) -> str:
    order_id = new_ulid()
    body = {
        "event_id": new_ulid(),
        "seq": 1,
        "order_id": order_id,
        "order_number": 1000,
        "channel": "pos",
        "fulfillment": "delivery",
        "lines": [
            {
                "id": new_ulid(),
                "menu_item_id": venue.item_id,
                "quantity": 1,
                "name_snapshot": "Masala Dosa",
                "unit_price_cents": 1850,
            }
        ],
    }
    resp = client.post("/orders", json=body, headers=auth(TILL_TOKEN))
    assert resp.status_code == 200, resp.text
    return order_id


def test_http_dispatch_track_callback_and_cancel() -> None:
    from support import api

    engine = make_engine()
    with Session(engine) as setup:
        venue = seed_venue(setup)
        location = setup.get(Location, venue.location_id)
        assert location is not None
        location.address = "1 Curry Lane, Brisbane"
        setup.commit()

    with api(engine) as client:
        order_id = _place_delivery_order_over_http(client, venue)
        delivery_id = new_ulid()

        # dispatch (till)
        disp = client.post(
            f"/orders/{order_id}/delivery",
            json={
                "delivery_id": delivery_id,
                "dropoff_name": "Priya",
                "dropoff_phone": "+61400000000",
                "dropoff_address": "12 Example St",
            },
            headers=auth(TILL_TOKEN),
        )
        assert disp.status_code == 200, disp.text
        assert disp.json()["status"] == "requested"

        # a retry replays
        retry = client.post(
            f"/orders/{order_id}/delivery",
            json={
                "delivery_id": delivery_id,
                "dropoff_name": "Priya",
                "dropoff_phone": "+61400000000",
                "dropoff_address": "12 Example St",
            },
            headers=auth(TILL_TOKEN),
        )
        assert retry.status_code == 200
        assert retry.json()["replayed"] is True

        # track (till)
        track = client.get(f"/orders/{order_id}/delivery", headers=auth(TILL_TOKEN))
        assert track.status_code == 200
        assert track.json()["delivery_id"] == delivery_id

        # courier callback (admin)
        cb = client.post(
            f"/deliveries/{delivery_id}/status",
            json={"status": "accepted", "courier_name": "Sam"},
            headers=auth(ADMIN_TOKEN),
        )
        assert cb.status_code == 200
        assert cb.json()["status"] == "accepted"
        assert cb.json()["courier_name"] == "Sam"

        # the till can't post a courier callback (admin only)
        forbidden = client.post(
            f"/deliveries/{delivery_id}/status",
            json={"status": "picked_up"},
            headers=auth(TILL_TOKEN),
        )
        assert forbidden.status_code == 403

        # cancel (till)
        cancel = client.post(
            f"/deliveries/{delivery_id}/cancel",
            json={"reason": "customer no-show"},
            headers=auth(TILL_TOKEN),
        )
        assert cancel.status_code == 200
        assert cancel.json()["status"] == "canceled"


def test_http_dispatch_rejects_dine_in_order() -> None:
    from support import api

    engine = make_engine()
    with Session(engine) as setup:
        venue = seed_venue(setup)
        location = setup.get(Location, venue.location_id)
        assert location is not None
        location.address = "1 Curry Lane"
        setup.commit()

    with api(engine) as client:
        order_id = new_ulid()
        client.post(
            "/orders",
            json={
                "event_id": new_ulid(),
                "seq": 1,
                "order_id": order_id,
                "order_number": 1000,
                "channel": "pos",
                "fulfillment": "dine_in",
                "lines": [
                    {
                        "id": new_ulid(),
                        "menu_item_id": venue.item_id,
                        "quantity": 1,
                        "name_snapshot": "Dosa",
                        "unit_price_cents": 1850,
                    }
                ],
            },
            headers=auth(TILL_TOKEN),
        )
        resp = client.post(
            f"/orders/{order_id}/delivery",
            json={
                "delivery_id": new_ulid(),
                "dropoff_name": "Priya",
                "dropoff_phone": "+61400000000",
                "dropoff_address": "12 Example St",
            },
            headers=auth(TILL_TOKEN),
        )
        assert resp.status_code == 422
        assert resp.json()["code"] == "not_a_delivery_order"


# --- CLI ----------------------------------------------------------------------------


def test_cli_dispatch_and_status_callback() -> None:
    # The CLI opens its own session on the engine; place the order on the same one.
    engine = make_engine()
    with Session(engine) as setup:
        venue = seed_venue(setup)
        location = setup.get(Location, venue.location_id)
        assert location is not None
        location.address = "1 Curry Lane"
        setup.commit()
        cli_order = new_ulid()
        ingest_event(
            setup,
            {
                "event_id": new_ulid(),
                "device_id": venue.device_id,
                "seq": 1,
                "location_id": venue.location_id,
                "business_date": TODAY,
                "occurred_at": T0,
                "type": "order.placed",
                "payload": {
                    "order_id": cli_order,
                    "order_number": 1000,
                    "channel": "pos",
                    "fulfillment": "delivery",
                    "lines": [
                        {
                            "id": new_ulid(),
                            "quantity": 1,
                            "name_snapshot": "Dosa",
                            "unit_price_cents": 1850,
                        }
                    ],
                },
            },
        )
        setup.commit()

    delivery_id = new_ulid()
    from datetime import timedelta

    with Session(engine) as cli_session:
        args = build_parser().parse_args(
            [
                "delivery-dispatch",
                "--location",
                venue.location_id,
                "--order",
                cli_order,
                "--name",
                "Priya",
                "--phone",
                "+61400000000",
                "--address",
                "12 Example St",
                "--delivery-id",
                delivery_id,
            ]
        )
        out = run(cli_session, args, pairing_ttl=timedelta(minutes=15))
        cli_session.commit()
    assert "Dispatched delivery" in out
    assert "requested" in out

    with Session(engine) as cli_session:
        args = build_parser().parse_args(
            ["delivery-status", "--delivery", delivery_id, "--status", "accepted", "--courier", "Sam"]
        )
        out = run(cli_session, args, pairing_ttl=timedelta(minutes=15))
        cli_session.commit()
    assert "is now accepted" in out

    with Session(engine) as check:
        d = check.get(Delivery, delivery_id)
        assert d is not None and d.status == "accepted" and d.courier_name == "Sam"
