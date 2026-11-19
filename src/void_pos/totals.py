"""Order line arithmetic, shared by validation, payments and reports.

A modifier's quantity is per unit of its item: two naan with one "extra butter" each is
an item line of quantity 2 and a modifier line of quantity 1, and the butter is charged
twice. So a modifier line's extended quantity is its own quantity times its item's.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol


class Line(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def parent_line_id(self) -> str | None: ...
    @property
    def quantity(self) -> int: ...
    @property
    def unit_price_cents(self) -> int: ...


class TaxedLine(Line, Protocol):
    @property
    def tax_rate_snapshot(self) -> Decimal | float: ...


def extended_quantities(lines: Iterable[Line]) -> dict[str, int]:
    """Each line's quantity for the whole order: an item's own, a modifier's times its item's."""
    lines = list(lines)
    item_qty = {line.id: line.quantity for line in lines if line.parent_line_id is None}
    return {
        line.id: line.quantity
        if line.parent_line_id is None
        else line.quantity * item_qty.get(line.parent_line_id, 1)
        for line in lines
    }


def total_cents(lines: Iterable[Line]) -> int:
    lines = list(lines)
    qty = extended_quantities(lines)
    return sum(line.unit_price_cents * qty[line.id] for line in lines)


def gst_cents(lines: Iterable[TaxedLine]) -> int:
    """The GST included in the lines' prices. Australian menu prices include GST, so a
    line at rate r carries r / (1 + r) of its price as tax. Amounts are summed per rate
    and rounded once per rate (half up), so the receipt and the closeout agree."""
    lines = list(lines)
    qty = extended_quantities(lines)
    by_rate: dict[Decimal, int] = defaultdict(int)
    for line in lines:
        by_rate[Decimal(str(line.tax_rate_snapshot))] += line.unit_price_cents * qty[line.id]
    return sum(
        int((amount * rate / (1 + rate)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        for rate, amount in by_rate.items()
        if rate > 0
    )


class Adjustment(Protocol):
    @property
    def id(self) -> str: ...
    @property
    def kind(self) -> str: ...  # discount | surcharge
    @property
    def basis(self) -> str: ...  # percent | amount
    @property
    def value(self) -> int: ...  # basis points, or cents


@dataclass(frozen=True)
class OrderMath:
    subtotal_cents: int  # the items
    adjustments: dict[str, int]  # adjustment id -> signed cents (discounts negative)
    total_cents: int
    gst_cents: int


def _share(cents: int, basis_points: int) -> int:
    return int((Decimal(cents) * basis_points / 10_000).quantize(Decimal(1), ROUND_HALF_UP))


def order_math(lines: Iterable[TaxedLine], adjustments: Iterable[Adjustment] = ()) -> OrderMath:
    """The order's money. Discounts come off the items' subtotal (never below zero), then
    surcharges go on what's left (a public-holiday surcharge on the discounted bill).
    GST: each rate's share of the items is scaled to the final total, so a discount
    carries its GST off with it and a surcharge carries GST like the items it's on."""
    lines = list(lines)
    adjustments = list(adjustments)
    subtotal = total_cents(lines)
    amounts: dict[str, int] = {}
    running = subtotal
    for adj in (a for a in adjustments if a.kind == "discount"):
        off = _share(subtotal, adj.value) if adj.basis == "percent" else adj.value
        off = min(off, max(running, 0))
        amounts[adj.id] = -off
        running -= off
    base = running
    for adj in (a for a in adjustments if a.kind == "surcharge"):
        on = _share(base, adj.value) if adj.basis == "percent" else adj.value
        amounts[adj.id] = on
        running += on
    if subtotal <= 0 or running == subtotal:
        return OrderMath(subtotal, amounts, running, gst_cents(lines))
    qty = extended_quantities(lines)
    by_rate: dict[Decimal, int] = defaultdict(int)
    for line in lines:
        by_rate[Decimal(str(line.tax_rate_snapshot))] += line.unit_price_cents * qty[line.id]
    gst = sum(
        int(
            (Decimal(amount) * running / subtotal * rate / (1 + rate)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        )
        for rate, amount in by_rate.items()
        if rate > 0
    )
    return OrderMath(subtotal, amounts, running, gst)
