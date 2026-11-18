from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from support import make_engine
from void_pos.events import apply_events, ingest_event
from void_pos.ids import new_ulid
from void_pos.models import (
    Customer,
    Device,
    Event,
    Location,
    LoyaltyLedger,
    MenuItem,
    Modifier,
    ModifierGroup,
    Order,
    OrderLine,
    Org,
    Payment,
)

TODAY = date(2026, 11, 14)
T0 = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)


def _session() -> Session:
    engine = make_engine()
    return Session(engine)


def _seed(s: Session) -> dict[str, Any]:
    org = Org(name="Madras Curry House")
    s.add(org)
    s.flush()
    loc = Location(org_id=org.id, name="Brisbane CBD")
    s.add(loc)
    s.flush()
    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    customer = Customer(org_id=org.id, location_id=loc.id, name="Asha")
    item = MenuItem(location_id=loc.id, name="Masala Dosa", category="Mains", base_price_cents=1850)
    group = ModifierGroup(location_id=loc.id, name="Spice level", min_select=0)
    s.add_all([device, customer, item, group])
    s.flush()
    modifier = Modifier(group_id=group.id, name="Extra hot", price_delta_cents=0)
    s.add(modifier)
    s.flush()
    return {"loc": loc, "device": device, "customer": customer, "item": item, "modifier": modifier}


def _evt(
    device: Device,
    loc: Location,
    *,
    type: str,
    payload: dict[str, Any],
    seq: int,
    occurred_at: datetime = T0,
    event_id: str | None = None,
) -> dict[str, Any]:
    return {
        "event_id": event_id or new_ulid(),
        "device_id": device.id,
        "seq": seq,
        "location_id": loc.id,
        "business_date": TODAY,
        "occurred_at": occurred_at,
        "type": type,
        "payload": payload,
    }


def _placed_payload(item: MenuItem, modifier: Modifier, *, order_id: str) -> dict[str, Any]:
    item_line_id = new_ulid()
    return {
        "order_id": order_id,
        "order_number": 1000,
        "channel": "pos",
        "fulfillment": "pickup",
        "lines": [
            {
                "id": item_line_id,
                "menu_item_id": item.id,
                "quantity": 1,
                "name_snapshot": item.name,
                "unit_price_cents": item.base_price_cents,
                "category_snapshot": item.category,
            },
            {
                "id": new_ulid(),
                "parent_line_id": item_line_id,
                "modifier_id": modifier.id,
                "quantity": 1,
                "name_snapshot": modifier.name,
                "unit_price_cents": modifier.price_delta_cents,
            },
        ],
    }


def test_order_placed_projects_order_and_lines() -> None:
    with _session() as s:
        d = _seed(s)
        order_id = new_ulid()
        res = ingest_event(
            s,
            _evt(
                d["device"], d["loc"], type="order.placed", seq=1,
                payload=_placed_payload(d["item"], d["modifier"], order_id=order_id),
            ),
        )
        s.commit()

        assert res.status == "applied"
        order = s.get(Order, order_id)
        assert order is not None and order.state == "open"
        lines = s.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).all()
        assert len(lines) == 2
        assert any(ol.parent_line_id is not None for ol in lines)  # the modifier child line
        assert s.get(Device, d["device"].id).last_seq == 1


def test_ingest_is_idempotent() -> None:
    with _session() as s:
        d = _seed(s)
        order_id = new_ulid()
        evt = _evt(
            d["device"], d["loc"], type="order.placed", seq=1,
            payload=_placed_payload(d["item"], d["modifier"], order_id=order_id),
        )
        assert ingest_event(s, evt).status == "applied"
        assert ingest_event(s, evt).status == "duplicate"  # same event_id replayed
        s.commit()

        assert s.scalar(select(func.count()).select_from(Event)) == 1
        assert s.scalar(select(func.count()).select_from(Order)) == 1
        assert s.scalar(select(func.count()).select_from(OrderLine)) == 2


def test_paid_then_voided_is_a_conflict() -> None:
    with _session() as s:
        d = _seed(s)
        order_id = new_ulid()
        ingest_event(
            s,
            _evt(d["device"], d["loc"], type="order.placed", seq=1,
                 payload=_placed_payload(d["item"], d["modifier"], order_id=order_id)),
        )
        r_paid = ingest_event(
            s,
            _evt(d["device"], d["loc"], type="order.paid", seq=2, payload={
                "order_id": order_id,
                "payment": {"id": new_ulid(), "provider": "stripe",
                            "external_ref": "pi_1", "amount_cents": 1850},
            }),
        )
        r_void = ingest_event(
            s,
            _evt(d["device"], d["loc"], type="order.voided", seq=3,
                 payload={"order_id": order_id}),
        )
        s.commit()

        assert r_paid.status == "applied"
        assert r_void.status == "conflict"
        assert s.get(Order, order_id).state == "paid"
        assert s.scalar(select(func.count()).select_from(Payment)) == 1


def test_conflict_resolution_is_deterministic() -> None:
    # The voided event occurred earlier, so it must win regardless of arrival order.
    for arrival in ("void_first", "paid_first"):
        with _session() as s:
            d = _seed(s)
            order_id = new_ulid()
            ingest_event(
                s,
                _evt(d["device"], d["loc"], type="order.placed", seq=1, occurred_at=T0,
                     payload=_placed_payload(d["item"], d["modifier"], order_id=order_id)),
            )
            voided = _evt(d["device"], d["loc"], type="order.voided", seq=2,
                          occurred_at=T0 + timedelta(minutes=1), payload={"order_id": order_id})
            paid = _evt(d["device"], d["loc"], type="order.paid", seq=3,
                        occurred_at=T0 + timedelta(minutes=2), payload={
                            "order_id": order_id,
                            "payment": {"id": new_ulid(), "provider": "cash", "amount_cents": 1850},
                        })
            batch = [voided, paid] if arrival == "void_first" else [paid, voided]
            apply_events(s, batch)
            s.commit()

            assert s.get(Order, order_id).state == "voided", arrival


def test_loyalty_accumulates_from_events() -> None:
    with _session() as s:
        d = _seed(s)
        events = [
            _evt(d["device"], d["loc"], type="loyalty.recorded", seq=1, payload={
                "id": new_ulid(), "customer_id": d["customer"].id, "points_delta": 10,
                "reason": "earn"}),
            _evt(d["device"], d["loc"], type="loyalty.recorded", seq=2, payload={
                "id": new_ulid(), "customer_id": d["customer"].id, "points_delta": -3,
                "reason": "redeem"}),
        ]
        apply_events(s, events)
        s.commit()

        balance = s.scalar(
            select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
                LoyaltyLedger.customer_id == d["customer"].id
            )
        )
        assert balance == 7


def test_unknown_event_type_is_ignored() -> None:
    with _session() as s:
        d = _seed(s)
        res = ingest_event(
            s, _evt(d["device"], d["loc"], type="order.teleported", seq=1, payload={})
        )
        s.commit()
        assert res.status == "ignored"
        assert s.scalar(select(func.count()).select_from(Event)) == 1  # still logged


def test_event_from_unknown_device_is_rejected() -> None:
    with _session() as s:
        d = _seed(s)
        stranger = Device(
            location_id=d["loc"].id, name="Ghost", order_number_start=1, order_number_end=9
        )
        stranger.id = new_ulid()  # never added to the session
        res = ingest_event(
            s,
            _evt(stranger, d["loc"], type="order.placed", seq=1,
                 payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid())),
        )
        assert (res.status, res.code) == ("rejected", "unknown_device")
        assert s.scalar(select(func.count()).select_from(Event)) == 0


def test_event_for_another_location_is_rejected() -> None:
    with _session() as s:
        d = _seed(s)
        other = Location(org_id=d["loc"].org_id, name="Gold Coast")
        s.add(other)
        s.flush()
        res = ingest_event(
            s,
            _evt(d["device"], other, type="order.placed", seq=1,
                 payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid())),
        )
        assert (res.status, res.code) == ("rejected", "location_mismatch")


def test_reused_seq_is_rejected_and_not_logged() -> None:
    with _session() as s:
        d = _seed(s)
        first = _evt(d["device"], d["loc"], type="order.placed", seq=1,
                     payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid()))
        second = _evt(d["device"], d["loc"], type="order.placed", seq=1,
                      payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid()))
        assert ingest_event(s, first).status == "applied"
        res = ingest_event(s, second)
        assert (res.status, res.code) == ("rejected", "seq_conflict")
        assert s.scalar(select(func.count()).select_from(Event)) == 1


def test_malformed_payload_is_rejected() -> None:
    with _session() as s:
        d = _seed(s)
        payload = _placed_payload(d["item"], d["modifier"], order_id=new_ulid())
        del payload["order_number"]
        res = ingest_event(s, _evt(d["device"], d["loc"], type="order.placed", seq=1, payload=payload))
        assert (res.status, res.code) == ("rejected", "invalid_payload")
        assert "order_number" in (res.detail or "")


def test_modifier_must_hang_off_an_item_in_the_same_order() -> None:
    with _session() as s:
        d = _seed(s)
        payload = _placed_payload(d["item"], d["modifier"], order_id=new_ulid())
        payload["lines"][1]["parent_line_id"] = new_ulid()
        res = ingest_event(s, _evt(d["device"], d["loc"], type="order.placed", seq=1, payload=payload))
        assert (res.status, res.code) == ("rejected", "invalid_payload")


def test_order_number_outside_the_devices_range_is_rejected() -> None:
    with _session() as s:
        d = _seed(s)
        payload = _placed_payload(d["item"], d["modifier"], order_id=new_ulid())
        payload["order_number"] = 5000  # the seeded till owns 1000-1999
        res = ingest_event(s, _evt(d["device"], d["loc"], type="order.placed", seq=1, payload=payload))
        assert (res.status, res.code) == ("rejected", "order_number_out_of_range")


def test_mixed_naive_and_aware_times_still_sort() -> None:
    with _session() as s:
        d = _seed(s)
        naive = _evt(d["device"], d["loc"], type="order.placed", seq=1,
                     payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid()),
                     occurred_at=datetime(2026, 11, 14, 9, 0))
        aware = _evt(d["device"], d["loc"], type="order.placed", seq=2,
                     payload=_placed_payload(d["item"], d["modifier"], order_id=new_ulid()))
        results = apply_events(s, [aware, naive])
        assert [r.status for r in results] == ["applied", "applied"]
