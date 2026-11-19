from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from support import make_engine, seed_venue
from void_pos.crm import customer_orders, normalize_phone, upsert_customer
from void_pos.errors import InvalidRequest
from void_pos.ids import new_ulid
from void_pos.models import Order


def test_phone_normalisation() -> None:
    assert normalize_phone("+61 400 000 000") == "+61400000000"
    assert normalize_phone("(07) 3000-1234") == "0730001234"
    assert normalize_phone("") is None
    assert normalize_phone(None) is None


def test_a_returning_customer_is_matched_by_phone() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        first = upsert_customer(
            s, org_id=venue.org_id, location_id=venue.location_id,
            name="Ada", phone="+61 400 000 000", email=None, marketing_opt_in=True,
        )
        again = upsert_customer(
            s, org_id=venue.org_id, location_id=venue.location_id,
            name="Ada L.", phone="+61-400-000-000", email="ada@x.io",
            marketing_opt_in=False,
        )
        assert again.id == first.id  # same record, matched on the normalised phone
        assert again.name == "Ada L." and again.email == "ada@x.io"
        assert again.marketing_opt_in is False  # latest consent wins


def test_match_by_email_when_no_phone() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        a = upsert_customer(
            s, org_id=venue.org_id, location_id=venue.location_id,
            name="Bo", phone=None, email="BO@Example.com", marketing_opt_in=False,
        )
        b = upsert_customer(
            s, org_id=venue.org_id, location_id=venue.location_id,
            name="Bo", phone=None, email="bo@example.com", marketing_opt_in=False,
        )
        assert a.id == b.id  # email is matched case-insensitively


def test_a_contact_detail_is_required() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        with pytest.raises(InvalidRequest):
            upsert_customer(
                s, org_id=venue.org_id, location_id=venue.location_id,
                name="No Contact", phone=None, email=None, marketing_opt_in=False,
            )


def test_history_is_newest_first() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        customer = upsert_customer(
            s, org_id=venue.org_id, location_id=venue.location_id,
            name="Ada", phone="+61400000000", email=None, marketing_opt_in=False,
        )
        ids = [new_ulid(), new_ulid(), new_ulid()]  # ULIDs sort in mint order
        for i, oid in enumerate(ids):
            s.add(
                Order(
                    id=oid, location_id=venue.location_id, device_id=venue.device_id,
                    customer_id=customer.id, order_number=5000 + i, business_date=date(2026, 11, 19),
                    channel="web", fulfillment="pickup", state="open",
                )
            )
        s.commit()
        history = customer_orders(s, customer.id)
        assert [o.id for o in history] == list(reversed(ids))
