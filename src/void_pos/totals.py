"""Order line arithmetic, shared by validation, payments and reports.

A modifier's quantity is per unit of its item: two naan with one "extra butter" each is
an item line of quantity 2 and a modifier line of quantity 1, and the butter is charged
twice. So a modifier line's extended quantity is its own quantity times its item's.
"""

from __future__ import annotations

from collections.abc import Iterable
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
