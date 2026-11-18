"""Load a venue's menu from a spreadsheet saved as CSV (UTF-8), to set up a venue.

Columns are matched by header, in any order; others are ignored:

    name            required
    price           in dollars, e.g. 18.50 (or base_price_cents, e.g. 1850)
    category        optional, e.g. Mains
    kitchen_name    optional, any script, e.g. மசாலா தோசை
    tax_rate        optional, 0.1 or 10% (default 0)
    is_available    optional, yes/no (default yes)

Items match the location's existing items by name (ignoring case): a match is updated with
whatever changed, a new name is created, and items missing from the file are left alone, so
the same sheet can be re-imported after a price change. Every create and update goes through
the menu service, so it lands in the change history and can be reverted. The whole file is
checked first; if any row is wrong nothing is applied and every problem is reported.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

from sqlalchemy.orm import Session

from ..errors import InvalidRequest
from .history import item_values
from .service import create_item, list_items, update_item

MAX_PRICE_CENTS = 10_000_000
_YES = {"", "yes", "y", "true", "1"}
_NO = {"no", "n", "false", "0"}


@dataclass(frozen=True)
class MenuRow:
    line: int
    name: str
    base_price_cents: int
    category: str | None
    kitchen_name: str | None
    tax_rate: float
    is_available: bool

    def fields(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "base_price_cents": self.base_price_cents,
            "category": self.category,
            "kitchen_name": self.kitchen_name,
            "tax_rate": self.tax_rate,
            "is_available": self.is_available,
        }


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


def _text(row: dict[str, str], key: str, limit: int) -> str | None:
    value = (row.get(key) or "").strip()
    if len(value) > limit:
        raise ValueError(f"{key} is longer than {limit} characters")
    return value or None


def _price_cents(row: dict[str, str]) -> int:
    if (row.get("price") or "").strip():
        raw = row["price"].strip().lstrip("$").replace(",", "")
        try:
            dollars = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"price {row['price']!r} isn't a number") from exc
        cents = dollars * 100
        if cents != cents.to_integral_value():
            raise ValueError(f"price {row['price']!r} has fractions of a cent")
        value = int(cents)
    elif (row.get("base_price_cents") or "").strip():
        try:
            value = int(row["base_price_cents"].strip())
        except ValueError as exc:
            raise ValueError(f"base_price_cents {row['base_price_cents']!r} isn't a whole number") from exc
    else:
        raise ValueError("price is missing")
    if not 0 <= value <= MAX_PRICE_CENTS:
        raise ValueError("price must be between $0 and $100,000")
    return value


def _tax_rate(row: dict[str, str]) -> float:
    raw = (row.get("tax_rate") or "").strip()
    if not raw:
        return 0.0
    try:
        rate = Decimal(raw[:-1]) / 100 if raw.endswith("%") else Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(f"tax_rate {raw!r} isn't a number") from exc
    if not 0 <= rate <= 1:
        raise ValueError("tax_rate must be between 0 and 1 (or 0% and 100%)")
    return float(rate)


def _available(row: dict[str, str]) -> bool:
    raw = (row.get("is_available") or "").strip().lower()
    if raw in _YES:
        return True
    if raw in _NO:
        return False
    raise ValueError(f"is_available {raw!r} should be yes or no")


def parse_menu_csv(text: str) -> list[MenuRow]:
    """Every row, or an InvalidRequest listing each problem by spreadsheet row number."""
    reader = csv.DictReader(StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        raise InvalidRequest("the file is empty", code="invalid_menu_file")
    reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]
    if "name" not in reader.fieldnames or not (
        {"price", "base_price_cents"} & set(reader.fieldnames)
    ):
        raise InvalidRequest(
            "the header row needs a name column and a price (or base_price_cents) column",
            code="invalid_menu_file",
        )

    rows: list[MenuRow] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for line, raw in enumerate(reader, start=2):  # row 1 is the header
        if not any((value or "").strip() for value in raw.values()):
            continue  # blank spreadsheet row
        try:
            name = _text(raw, "name", 200)
            if name is None:
                raise ValueError("name is missing")
            if name.casefold() in seen:
                raise ValueError(f"{name!r} is also on row {seen[name.casefold()]}")
            seen[name.casefold()] = line
            rows.append(
                MenuRow(
                    line=line,
                    name=name,
                    base_price_cents=_price_cents(raw),
                    category=_text(raw, "category", 120),
                    kitchen_name=_text(raw, "kitchen_name", 200),
                    tax_rate=_tax_rate(raw),
                    is_available=_available(raw),
                )
            )
        except ValueError as exc:
            problems.append(f"row {line}: {exc}")
    if problems:
        raise InvalidRequest("; ".join(problems), code="invalid_menu_file", problems=problems)
    if not rows:
        raise InvalidRequest("the file has no menu items", code="invalid_menu_file")
    return rows


def import_menu(
    session: Session, location_id: str, rows: list[MenuRow], *, dry_run: bool = False
) -> ImportResult:
    """Create or update each row's item at the location. With ``dry_run`` nothing is
    written; the result says what would happen."""
    existing = {item.name.casefold(): item for item in list_items(session, location_id)}
    result = ImportResult()
    for row in rows:
        item = existing.get(row.name.casefold())
        if item is None:
            if not dry_run:
                create_item(session, location_id=location_id, **row.fields())
            result.created.append(row.name)
            continue
        current = item_values(item)
        changes = {k: v for k, v in row.fields().items() if current[k] != v}
        if not changes:
            result.unchanged.append(row.name)
            continue
        if not dry_run:
            update_item(session, item.id, changes)
        result.updated.append(row.name)
    return result
