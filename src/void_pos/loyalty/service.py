"""Loyalty ledger: balance, earn/redeem arithmetic, and recording entries.

Earning: whole points per whole dollar spent (``earn_points_per_dollar``). Redeeming: each
point is worth ``redeem_cents_per_point`` cents off the bill, capped at the balance and at
what is owing. Both are recorded as ``loyalty.recorded`` events (positive delta to earn,
negative to redeem), so the ledger keeps the whole history for export and disputes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clock import local_date
from ..commands import Command, record
from ..config import Settings
from ..devices import get_location
from ..events import EventType
from ..ids import new_ulid
from ..models import Device, LoyaltyLedger, Order


@dataclass(frozen=True)
class LoyaltyRules:
    """How points are earned and what they are worth, from the venue's settings."""

    enabled: bool
    earn_points_per_dollar: int
    redeem_cents_per_point: int


def rules_from_settings(settings: Settings) -> LoyaltyRules:
    return LoyaltyRules(
        enabled=settings.loyalty_enabled,
        earn_points_per_dollar=settings.loyalty_earn_points_per_dollar,
        redeem_cents_per_point=settings.loyalty_redeem_cents_per_point,
    )


def balance(session: Session, customer_id: str) -> int:
    """The customer's point balance: the sum of every ledger delta."""
    total = session.scalar(
        select(func.coalesce(func.sum(LoyaltyLedger.points_delta), 0)).where(
            LoyaltyLedger.customer_id == customer_id
        )
    )
    return int(total or 0)


def points_earned(spend_cents: int, rules: LoyaltyRules) -> int:
    """Points for spending ``spend_cents``: whole points per whole dollar. Zero when
    loyalty earning is off."""
    if not rules.enabled or rules.earn_points_per_dollar <= 0 or spend_cents <= 0:
        return 0
    return (spend_cents // 100) * rules.earn_points_per_dollar


def max_redeemable_points(point_balance: int, owing_cents: int, rules: LoyaltyRules) -> int:
    """The most points that can be put toward ``owing_cents``: never more than the balance,
    never more than the bill. Zero when the points have no redemption value configured."""
    if point_balance <= 0 or owing_cents <= 0 or rules.redeem_cents_per_point <= 0:
        return 0
    by_bill = owing_cents // rules.redeem_cents_per_point
    return max(0, min(point_balance, by_bill))


def redemption_value_cents(points: int, rules: LoyaltyRules) -> int:
    """What ``points`` are worth off the bill, in cents."""
    return max(0, points) * rules.redeem_cents_per_point


def record_entry(
    session: Session,
    device: Device,
    command: Command,
    *,
    customer_id: str,
    points_delta: int,
    reason: str,
    order_id: str | None = None,
) -> str:
    """Record one ledger entry (``loyalty.recorded``) through the event pipeline and return
    its id. A positive delta earns, a negative delta redeems."""
    ledger_id = new_ulid()
    record(
        session,
        device,
        command,
        event_type=EventType.LOYALTY_RECORDED,
        payload={
            "id": ledger_id,
            "customer_id": customer_id,
            "order_id": order_id,
            "points_delta": points_delta,
            "reason": reason,
        },
        business_date=_business_date(session, device, order_id),
    )
    return ledger_id


def _business_date(session: Session, device: Device, order_id: str | None) -> date:
    """A loyalty entry carries the order's business date when it has one, else today in the
    location's zone."""
    if order_id is not None:
        order = session.get(Order, order_id)
        if order is not None:
            return order.business_date
    return local_date(get_location(session, device.location_id).timezone)
