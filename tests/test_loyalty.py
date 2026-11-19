from __future__ import annotations

from sqlalchemy.orm import Session

from support import make_engine, seed_venue
from void_pos.ids import new_ulid
from void_pos.loyalty import (
    LoyaltyRules,
    balance,
    max_redeemable_points,
    points_earned,
    redemption_value_cents,
)
from void_pos.models import LoyaltyLedger

ON = LoyaltyRules(enabled=True, earn_points_per_dollar=1, redeem_cents_per_point=1)
OFF = LoyaltyRules(enabled=False, earn_points_per_dollar=1, redeem_cents_per_point=1)


def test_points_are_earned_per_whole_dollar() -> None:
    assert points_earned(1850, ON) == 18  # $18.50 -> 18 points
    assert points_earned(999, ON) == 9
    assert points_earned(0, ON) == 0
    assert points_earned(1850, LoyaltyRules(True, 10, 1)) == 180  # 10 per dollar


def test_nothing_is_earned_when_loyalty_is_off() -> None:
    assert points_earned(5000, OFF) == 0


def test_redeemable_points_are_capped_by_balance_and_by_the_bill() -> None:
    # Plenty owing, balance is the limit.
    assert max_redeemable_points(50, 10_000, ON) == 50
    # Plenty of points, the bill is the limit (1 cent per point -> at most `owing` points).
    assert max_redeemable_points(10_000, 300, ON) == 300
    # Each point worth 5c: a $3 bill takes at most 60 points.
    assert max_redeemable_points(10_000, 300, LoyaltyRules(True, 1, 5)) == 60
    assert max_redeemable_points(0, 300, ON) == 0
    assert max_redeemable_points(50, 0, ON) == 0


def test_redemption_value() -> None:
    assert redemption_value_cents(18, ON) == 18
    assert redemption_value_cents(18, LoyaltyRules(True, 1, 5)) == 90
    assert redemption_value_cents(-3, ON) == 0


def test_balance_is_the_sum_of_every_ledger_delta() -> None:
    engine = make_engine()
    with Session(engine) as s:
        venue = seed_venue(s)
        customer_id = _seed_customer(s, venue.org_id)
        for delta, reason in [(18, "earn"), (-5, "redeem"), (10, "earn")]:
            s.add(
                LoyaltyLedger(
                    location_id=venue.location_id,
                    customer_id=customer_id,
                    points_delta=delta,
                    reason=reason,
                )
            )
        s.commit()
        assert balance(s, customer_id) == 23
        assert balance(s, new_ulid()) == 0  # unknown customer


def _seed_customer(session: Session, org_id: str) -> str:
    from void_pos.models import Customer

    customer = Customer(org_id=org_id, name="Ada", phone="+61400000000")
    session.add(customer)
    session.flush()
    return customer.id
