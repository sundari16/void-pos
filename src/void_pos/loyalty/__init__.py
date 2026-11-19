"""Loyalty: earn and redeem points on the append-only ledger.

The balance is always ``SUM(points_delta)`` — never a stored field — so earning and
redeeming are just ledger entries recorded through the same event pipeline as orders
(``loyalty.recorded``). The earn/redeem arithmetic is pure and lives in ``service`` so the
till, the storefront and tests all compute the same numbers.
"""

from __future__ import annotations

from .service import (
    LoyaltyRules,
    balance,
    max_redeemable_points,
    points_earned,
    record_entry,
    redemption_value_cents,
    rules_from_settings,
)

__all__ = [
    "LoyaltyRules",
    "balance",
    "max_redeemable_points",
    "points_earned",
    "record_entry",
    "redemption_value_cents",
    "rules_from_settings",
]
