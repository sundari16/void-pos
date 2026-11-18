"""Order line arithmetic, shared by validation, payments and reports.

A modifier's quantity is per unit of its item: two naan with one "extra butter" each is
an item line of quantity 2 and a modifier line of quantity 1, and the butter is charged
twice. So a modifier line's extended quantity is its own quantity times its item's.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
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
