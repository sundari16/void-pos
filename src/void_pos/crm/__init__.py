"""Customer records (CRM): find-or-create a customer and read their order history.

A customer belongs to the org, so any of its locations shares them. The full order
history is the data asset — kept here as plain reference data the storefront and the
loyalty ledger both point at.
"""

from __future__ import annotations

from .service import (
    customer_orders,
    get_customer,
    normalize_email,
    normalize_phone,
    upsert_customer,
)

__all__ = [
    "customer_orders",
    "get_customer",
    "normalize_email",
    "normalize_phone",
    "upsert_customer",
]
