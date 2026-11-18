from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from support import add_till, make_engine, seed_venue
from void_pos.commands import Command
from void_pos.errors import Conflict, InvalidRequest, NotFound, PaymentFailed
from void_pos.events import ingest_event
from void_pos.ids import new_ulid
from void_pos.models import Device, Event, Order, Payment
from void_pos.payments import (
    CashProvider,
    ChargeRequest,
    ChargeResult,
    ExternalProvider,
    balance_cents,
    order_total_cents,
    pay_order,
)

TODAY = date(2026, 11, 15)
T0 = datetime(2026, 11, 15, 10, 0, tzinfo=UTC)


def _session() -> Session:
    return Session(make_engine())


def _place(
    s: Session,
    *,
    quantity: int = 2,
    price: int = 1850,
    modifiers: tuple[tuple[int, int], ...] = (),
) -> tuple[Device, str]:
    """Place one item line, plus a modifier child line per (quantity, price) pair."""
    venue = seed_venue(s)
    device = s.get(Device, venue.device_id)
    assert device is not None
    order_id = new_ulid()
    item_line_id = new_ulid()
    mod_lines = [
        {
            "id": new_ulid(),
            "parent_line_id": item_line_id,
            "quantity": mod_qty,
            "name_snapshot": "Extra",
            "unit_price_cents": mod_price,
        }
        for mod_qty, mod_price in modifiers
    ]
    ingest_event(
        s,
        {
            "event_id": new_ulid(),
            "device_id": device.id,
            "seq": 1,
            "location_id": venue.location_id,
            "business_date": TODAY,
            "occurred_at": T0,
            "type": "order.placed",
            "payload": {
                "order_id": order_id,
                "order_number": 1000,
                "channel": "pos",
                "fulfillment": "pickup",
                "lines": [
                    {
                        "id": item_line_id,
                        "menu_item_id": venue.item_id,
                        "quantity": quantity,
                        "name_snapshot": "Masala Dosa",
                        "unit_price_cents": price,
                    },
                    *mod_lines,
                ],
            },
        },
    )
    s.commit()
    return device, order_id


def _cmd(seq: int) -> Command:
    return Command(event_id=new_ulid(), seq=seq)


class FailingProvider:
    name = "card"

    def charge(self, request: ChargeRequest) -> ChargeResult:
        return ChargeResult("card", None, request.amount_cents, request.currency, "failed")


def test_order_total_sums_lines() -> None:
    with _session() as s:
        _, order_id = _place(s, quantity=2, price=1850)
        assert order_total_cents(s, order_id) == 3700


def test_modifiers_are_charged_per_unit_of_their_item() -> None:
    with _session() as s:
        # 2 dosa, each with extra cheese (150) and 2 extra chutneys (50 each), less a
        # 100 discount modifier: 2 x (1850 + 150 + 100 - 100).
        _, order_id = _place(s, quantity=2, price=1850, modifiers=((1, 150), (2, 50), (1, -100)))
        assert order_total_cents(s, order_id) == 2 * 2000


def test_cash_payment_captures_and_marks_paid() -> None:
    with _session() as s:
        device, order_id = _place(s)
        pid = new_ulid()
        outcome = pay_order(s, device, order_id, CashProvider(), _cmd(2), payment_id=pid)
        s.commit()

        assert outcome.replayed is False
        assert outcome.payment.id == pid
        assert outcome.payment.provider == "cash"
        assert outcome.payment.external_ref is None
        assert outcome.payment.amount_cents == 3700  # defaulted to the order total
        assert s.get(Order, order_id).state == "paid"
        assert s.scalar(select(func.count()).select_from(Payment)) == 1


def test_external_payment_records_ref_not_as_key() -> None:
    with _session() as s:
        device, order_id = _place(s)
        outcome = pay_order(
            s,
            device,
            order_id,
            ExternalProvider(external_ref="pi_test_123"),
            _cmd(2),
            payment_id=new_ulid(),
            amount_cents=3700,
        )
        s.commit()

        assert outcome.payment.provider == "external"
        assert outcome.payment.external_ref == "pi_test_123"
        assert outcome.payment.id != "pi_test_123"  # keyed on our own ULID, never the provider id


def test_replayed_payment_returns_the_original_without_charging_again() -> None:
    with _session() as s:
        device, order_id = _place(s)
        command, pid = _cmd(2), new_ulid()
        first = pay_order(s, device, order_id, CashProvider(), command, payment_id=pid)
        s.commit()
        again = pay_order(s, device, order_id, FailingProvider(), command, payment_id=pid)

        assert again.replayed is True
        assert again.payment.id == first.payment.id
        assert s.scalar(select(func.count()).select_from(Payment)) == 1


def test_paying_a_paid_order_is_refused_before_charging() -> None:
    with _session() as s:
        device, order_id = _place(s)
        pay_order(s, device, order_id, CashProvider(), _cmd(2), payment_id=new_ulid())
        s.commit()
        with pytest.raises(Conflict) as exc:
            pay_order(s, device, order_id, CashProvider(), _cmd(3), payment_id=new_ulid())
        assert exc.value.code == "order_closed"


def test_a_split_bill_is_paid_once_its_payments_cover_the_total() -> None:
    with _session() as s:
        device, order_id = _place(s)  # 3700

        def pay(seq: int, amount: int | None, provider: Any = None) -> Any:
            return pay_order(
                s,
                device,
                order_id,
                provider or CashProvider(),
                _cmd(seq),
                payment_id=new_ulid(),
                amount_cents=amount,
            )

        first = pay(2, 1000)
        assert (first.balance_cents, first.order.state) == (2700, "open")
        second = pay(3, 1500, ExternalProvider(external_ref="EFTPOS 4471"))
        assert (second.balance_cents, second.order.state) == (1200, "open")
        assert balance_cents(s, order_id) == 1200

        with pytest.raises(InvalidRequest) as over:
            pay(4, 2000)
        assert (over.value.code, over.value.extra["balance_cents"]) == ("amount_exceeds_balance", 1200)
        with pytest.raises(InvalidRequest) as zero:
            pay(4, 0)
        assert zero.value.code == "invalid_amount"

        last = pay(4, None)  # the rest
        assert (last.payment.amount_cents, last.balance_cents, last.order.state) == (1200, 0, "paid")
        with pytest.raises(Conflict) as closed:
            pay(5, None)
        assert closed.value.code == "order_closed"
        assert s.scalar(select(func.count()).select_from(Payment)) == 3


def test_a_free_order_can_be_settled_with_nothing() -> None:
    with _session() as s:
        device, order_id = _place(s, quantity=1, price=0)
        outcome = pay_order(s, device, order_id, CashProvider(), _cmd(2), payment_id=new_ulid())
        assert (outcome.payment.amount_cents, outcome.order.state) == (0, "paid")


def test_synced_part_payments_settle_the_order_when_they_cover_it() -> None:
    with _session() as s:
        device, order_id = _place(s)

        def paid(seq: int, amount: int) -> str:
            result = ingest_event(
                s,
                {
                    "event_id": new_ulid(),
                    "device_id": device.id,
                    "seq": seq,
                    "location_id": device.location_id,
                    "business_date": TODAY,
                    "occurred_at": T0,
                    "type": "order.paid",
                    "payload": {
                        "order_id": order_id,
                        "payment": {"id": new_ulid(), "provider": "cash", "amount_cents": amount},
                    },
                },
            )
            return result.detail or result.status

        assert paid(2, 2000) == "part payment; 1700 cents owing"
        assert s.get(Order, order_id).state == "open"
        paid(3, 1700)
        assert s.get(Order, order_id).state == "paid"


def test_failed_charge_records_nothing() -> None:
    with _session() as s:
        device, order_id = _place(s)
        with pytest.raises(PaymentFailed):
            pay_order(s, device, order_id, FailingProvider(), _cmd(2), payment_id=new_ulid())
        assert s.scalar(select(func.count()).select_from(Event)) == 1  # just the placement
        assert s.get(Order, order_id).state == "open"


def test_pay_unknown_order_raises() -> None:
    with _session() as s:
        device, _ = _place(s)
        with pytest.raises(NotFound):
            pay_order(
                s, device, "00000000000000000000000000", CashProvider(), _cmd(2), payment_id=new_ulid()
            )


def test_a_till_cannot_pay_another_locations_order() -> None:
    with _session() as s:
        _, order_id = _place(s)
        other = seed_venue(s, location_name="Gold Coast", token="gold-coast-till")
        stranger = s.get(Device, other.device_id)
        assert stranger is not None
        with pytest.raises(NotFound):
            pay_order(s, stranger, order_id, CashProvider(), _cmd(1), payment_id=new_ulid())


def test_a_second_till_at_the_same_location_can_settle() -> None:
    with _session() as s:
        device, order_id = _place(s)
        till2 = add_till(s, device.location_id, name="Till 2", token="t2", order_range=(2000, 2999))
        outcome = pay_order(s, till2, order_id, CashProvider(), _cmd(1), payment_id=new_ulid())
        assert outcome.order.state == "paid"
