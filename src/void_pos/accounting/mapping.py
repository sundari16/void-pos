"""The per-venue account-code mapping for the Xero daily-takings invoice.

Sensible Australian defaults sit here; a venue overrides any of them (its own chart of
accounts) with ``void-pos xero-accounts --file map.json``, stored as JSON on the Xero
connection. ``void-pos xero-push --dry-run`` prints the mapping in force so the operator
checks the codes against their Xero chart before the first real push.

Account **codes** are the short codes from the venue's Xero chart of accounts (e.g. ``200``
for Sales). ``tax_type`` values are Xero AU tax-type codes: ``OUTPUT`` is GST on Income,
``EXEMPTOUTPUT`` is GST Free Income.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from ..errors import InvalidRequest

# Xero AU defaults. Bank/clearing codes especially must be mapped to the venue's own chart;
# the defaults point at the demo-company codes and are only a starting point.
DEFAULT_SALES_ACCOUNT = "200"  # Sales
DEFAULT_ADJUSTMENT_ACCOUNT = "200"  # discounts/surcharges net against sales by default
DEFAULT_TIPS_ACCOUNT = "200"  # point at a tips-payable/clearing account for the venue
DEFAULT_ROUNDING_ACCOUNT = "860"  # Rounding
DEFAULT_CLEARING_ACCOUNT = "090"  # Business Bank Account


@dataclass(frozen=True)
class AccountMap:
    """Where each part of the day's takings lands in Xero."""

    sales_account: str = DEFAULT_SALES_ACCOUNT
    # Per revenue class → account code; a class not listed uses ``sales_account``.
    class_accounts: dict[str, str] = field(default_factory=dict)
    # Revenue classes sold GST-free (basic groceries etc.); the rest use ``tax_type``.
    gst_free_classes: list[str] = field(default_factory=list)
    discounts_account: str = DEFAULT_ADJUSTMENT_ACCOUNT
    surcharges_account: str = DEFAULT_ADJUSTMENT_ACCOUNT
    tips_account: str = DEFAULT_TIPS_ACCOUNT
    rounding_account: str = DEFAULT_ROUNDING_ACCOUNT
    # Where each provider's takings are banked. A provider not listed uses ``default_clearing``.
    clearing_accounts: dict[str, str] = field(
        default_factory=lambda: {"cash": DEFAULT_CLEARING_ACCOUNT}
    )
    default_clearing: str = DEFAULT_CLEARING_ACCOUNT
    # Xero AU tax-type codes.
    tax_type: str = "OUTPUT"  # GST on Income
    gst_free_tax_type: str = "EXEMPTOUTPUT"  # GST Free Income
    contact_name: str = "Daily Takings"

    def account_for_class(self, revenue_class: str) -> str:
        return self.class_accounts.get(revenue_class, self.sales_account)

    def tax_type_for_class(self, revenue_class: str) -> str:
        return self.gst_free_tax_type if revenue_class in self.gst_free_classes else self.tax_type

    def clearing_for(self, provider: str) -> str:
        return self.clearing_accounts.get(provider, self.default_clearing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sales_account": self.sales_account,
            "class_accounts": dict(self.class_accounts),
            "gst_free_classes": list(self.gst_free_classes),
            "discounts_account": self.discounts_account,
            "surcharges_account": self.surcharges_account,
            "tips_account": self.tips_account,
            "rounding_account": self.rounding_account,
            "clearing_accounts": dict(self.clearing_accounts),
            "default_clearing": self.default_clearing,
            "tax_type": self.tax_type,
            "gst_free_tax_type": self.gst_free_tax_type,
            "contact_name": self.contact_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> AccountMap:
        """Build a map from stored/overriding JSON, keeping the defaults for absent keys."""
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise InvalidRequest(
                f"unknown account-map key(s): {', '.join(sorted(unknown))}",
                code="xero_bad_account_map",
            )
        return cls(**{k: v for k, v in data.items() if k in known})
