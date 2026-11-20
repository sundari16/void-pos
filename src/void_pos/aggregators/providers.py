"""Open-core aggregator adapters.

``MockAggregator`` is the built-in fake — the aggregator counterpart of
:class:`~void_pos.payments.providers.CashProvider`. It speaks a small, readable JSON order
shape so the whole ingestion → kitchen → sales pipeline is testable end to end without a
partner. It records the accept/reject acknowledgements it is told to send, so a test (or
the CLI simulator) can see them, and it never reaches the network.

The real Uber Eats, DoorDash and Menulog adapters each implement
:class:`AggregatorProvider` against that partner's signed API and live in the commercial
layer — they need partner onboarding and approval, so they are deliberately not in this
open-core repo. ``build_provider`` names them and refuses to guess an API for them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..errors import NotFound
from .provider import (
    AggregatorPayloadError,
    MenuPublishResult,
    MenuSnapshot,
    NormalizedLine,
    NormalizedModifier,
    NormalizedOrder,
)

# Platforms whose adapters are commercial (signed partner API, partner approval required)
# and therefore not shipped in the open core. Named here only so the seam can say so.
COMMERCIAL_PROVIDERS = ("uber_eats", "doordash", "menulog")


def _require(raw: Mapping[str, Any], key: str) -> Any:
    if key not in raw or raw[key] in (None, ""):
        raise AggregatorPayloadError(f"the order is missing {key!r}")
    return raw[key]


def _int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AggregatorPayloadError(f"{key} must be a whole number of cents")
    return value


def _placed_at(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise AggregatorPayloadError("placed_at must be an ISO 8601 timestamp") from exc


@dataclass
class MockAggregator:
    """A fake delivery partner for development and tests. It reads a plain order shape
    (see ``normalize_order``), echoes the catalog back as its "menu", and remembers the
    accept/reject calls it is told to make."""

    name: str = "mock"
    accepted: list[tuple[str, int | None]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    published: list[Mapping[str, Any]] = field(default_factory=list)

    def normalize_order(self, raw: Mapping[str, Any]) -> NormalizedOrder:
        items = _require(raw, "items")
        if not isinstance(items, list) or not items:
            raise AggregatorPayloadError("the order needs at least one item")
        lines = [self._line(item) for item in items]
        return NormalizedOrder(
            external_order_id=str(_require(raw, "id")),
            lines=lines,
            fulfillment=str(raw.get("fulfillment") or "delivery"),
            display_id=_opt_str(raw.get("display_id")),
            external_payment_ref=_opt_str(raw.get("payment_ref")),
            currency=str(raw.get("currency") or "AUD"),
            customer_name=_opt_str(raw.get("customer_name")),
            notes=_opt_str(raw.get("notes")),
            placed_at=_placed_at(raw.get("placed_at")),
        )

    def _line(self, item: Any) -> NormalizedLine:
        if not isinstance(item, Mapping):
            raise AggregatorPayloadError("each item must be an object")
        mods = item.get("modifiers") or []
        if not isinstance(mods, list):
            raise AggregatorPayloadError("an item's modifiers must be a list")
        return NormalizedLine(
            name=str(_require(item, "name")),
            unit_price_cents=_int(_require(item, "unit_price_cents"), "unit_price_cents"),
            quantity=_int(item.get("quantity", 1), "quantity"),
            kitchen_name=_opt_str(item.get("kitchen_name")),
            category=_opt_str(item.get("category")),
            tax_rate=float(item.get("tax_rate") or 0.0),
            menu_item_id=_opt_str(item.get("menu_item_id")),
            modifiers=[self._modifier(mod) for mod in mods],
        )

    def _modifier(self, mod: Any) -> NormalizedModifier:
        if not isinstance(mod, Mapping):
            raise AggregatorPayloadError("each modifier must be an object")
        return NormalizedModifier(
            name=str(_require(mod, "name")),
            unit_price_cents=_int(mod.get("unit_price_cents", 0), "unit_price_cents"),
            quantity=_int(mod.get("quantity", 1), "quantity"),
            kitchen_name=_opt_str(mod.get("kitchen_name")),
            modifier_id=_opt_str(mod.get("modifier_id")),
        )

    def build_menu(self, snapshot: MenuSnapshot) -> Mapping[str, Any]:
        """Echo the catalog as a simple menu document — enough to prove the sync-out seam
        end to end. A real adapter maps these fields onto its platform's menu schema."""
        return {
            "location_id": snapshot.location_id,
            "currency": snapshot.currency,
            "items": [
                {
                    "id": item.id,
                    "name": item.name,
                    "price_cents": item.price_cents,
                    "category": item.category,
                    "tax_rate": item.tax_rate,
                    "available": item.available,
                    "modifier_group_ids": list(item.modifier_group_ids),
                }
                for item in snapshot.items
            ],
            "modifier_groups": [
                {
                    "id": group.id,
                    "name": group.name,
                    "min_select": group.min_select,
                    "max_select": group.max_select,
                    "modifiers": [
                        {"id": m.id, "name": m.name, "price_delta_cents": m.price_delta_cents}
                        for m in group.modifiers
                    ],
                }
                for group in snapshot.groups
            ],
        }

    def publish_menu(self, payload: Mapping[str, Any]) -> MenuPublishResult:
        self.published.append(payload)
        items = payload.get("items") or []
        return MenuPublishResult(
            provider=self.name,
            status="published",
            item_count=len(items),
            external_ref=f"mock-menu-{len(self.published)}",
        )

    def accept(self, external_order_id: str, *, prep_minutes: int | None = None) -> None:
        self.accepted.append((external_order_id, prep_minutes))

    def reject(self, external_order_id: str, *, reason: str) -> None:
        self.rejected.append((external_order_id, reason))


def _opt_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def build_provider(name: str) -> MockAggregator:
    """The adapter for a platform slug. The open core ships only the mock; the real
    partner adapters are commercial (signed partner API, partner approval) and are refused
    here with a clear reason rather than a guessed-at integration."""
    if name == "mock":
        return MockAggregator()
    if name in COMMERCIAL_PROVIDERS:
        raise NotFound(
            f"the {name} adapter is a commercial module (it needs {name} partner approval "
            "and signed API access); the open core ships only the 'mock' provider",
            code="commercial_provider",
        )
    raise NotFound(f"unknown aggregator provider {name!r}", code="unknown_provider")
