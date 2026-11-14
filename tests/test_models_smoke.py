from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from void_pos.models import (
    Base,
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

TODAY = date(2026, 11, 13)


def _seed(session: Session) -> dict[str, object]:
    org = Org(name="Madras Curry House")
    session.add(org)
    session.flush()

    loc = Location(org_id=org.id, name="Brisbane CBD")
    session.add(loc)
    session.flush()

    device = Device(
        location_id=loc.id, name="Till 1", order_number_start=1000, order_number_end=1999
    )
    customer = Customer(org_id=org.id, location_id=loc.id, name="Asha", phone="+61400000000")
    item = MenuItem(
        location_id=loc.id,
        name="Masala Dosa",
        kitchen_name="மசாலா தୋசை",
        category="Mains",
        base_price_cents=1850,
        tax_rate=Decimal("0.1000"),
    )
    group = ModifierGroup(location_id=loc.id, name="Spice level", min_select=1, max_select=1)
    session.add_all([device, customer, item, group])
    session.flush()

    modifier = Modifier(group_id=group.id, name="Extra hot", price_delta_cents=0)
    session.add(modifier)
    session.flush()

    return {"loc": loc, "device": device, "customer": customer, "item": item, "modifier": modifier}


def test_order_with_modifier_child_line_and_projections() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        seed = _seed(s)
        loc, device, customer, item, modifier = (
            seed["loc"], seed["device"], seed["customer"], seed["item"], seed["modifier"]
        )

        order = Order(
            location_id=loc.id,
            device_id=device.id,
            customer_id=customer.id,
            order_number=device.order_number_start,
            business_date=TODAY,
            channel="pos",
            fulfillment="pickup",
        )
        s.add(order)
        s.flush()

        item_line = OrderLine(
            order_id=order.id,
            menu_item_id=item.id,
            quantity=1,
            name_snapshot=item.name,
            kitchen_name_snapshot=item.kitchen_name,
            unit_price_cents=item.base_price_cents,
            tax_rate_snapshot=item.tax_rate,
            category_snapshot=item.category,
        )
        s.add(item_line)
        s.flush()

        # A modifier selection is a child line pointing at its parent item line.
        s.add(
            OrderLine(
                order_id=order.id,
                parent_line_id=item_line.id,
                modifier_id=modifier.id,
                quantity=1,
                name_snapshot=modifier.name,
                unit_price_cents=modifier.price_delta_cents,
            )
        )
        s.add(
            Payment(
                order_id=order.id,
                location_id=loc.id,
                business_date=TODAY,
                provider="stripe",
                external_ref="pi_test_123",
                amount_cents=1850,
            )
        )
        s.add_all(
            [
                LoyaltyLedger(
                    location_id=loc.id, customer_id=customer.id, order_id=order.id,
                    points_delta=10, reason="earn",
                ),
                LoyaltyLedger(
                    location_id=loc.id, customer_id=customer.id, order_id=order.id,
                    points_delta=-3, reason="redeem",
                ),
            ]
        )
        s.add(
            Event(
                device_id=device.id,
                seq=1,
                location_id=loc.id,
                business_date=TODAY,
                occurred_at=datetime.now(UTC),
                type="order.placed",
                payload={"order_id": order.id},
            )
        )
        s.commit()

        lines = s.scalars(select(OrderLine).where(OrderLine.order_id == order.id)).all()
        assert len(lines) == 2
        child = next(ol for ol in lines if ol.parent_line_id is not None)
        assert child.parent_line_id == item_line.id

        # Payment keyed on our own id; the provider's id lives only in external_ref.
        pay = s.scalars(select(Payment).where(Payment.order_id == order.id)).one()
        assert pay.external_ref == "pi_test_123"
        assert pay.id != "pi_test_123"

        # Loyalty balance is a projection of the ledger, never a stored field.
        balance = s.scalar(
            select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
                LoyaltyLedger.customer_id == customer.id
            )
        )
        assert balance == 7


def test_event_seq_is_unique_per_device() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as s:
        seed = _seed(s)
        device, loc = seed["device"], seed["loc"]
        common = {
            "device_id": device.id,
            "location_id": loc.id,
            "business_date": TODAY,
            "occurred_at": datetime.now(UTC),
            "type": "noop",
            "payload": {},
        }
        s.add(Event(seq=1, **common))
        s.add(Event(seq=1, **common))
        try:
            s.commit()
            raise AssertionError("expected a uniqueness violation on (device_id, seq)")
        except IntegrityError:
            s.rollback()
