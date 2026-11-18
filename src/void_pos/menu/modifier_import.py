"""Load a venue's modifier groups from a spreadsheet saved as CSV (UTF-8).

One row per modifier; rows with the same group name make up that group. Columns are matched
by header, in any order; others are ignored:

    group           required, e.g. Spice level
    modifier        required, e.g. Hot
    price           the price change in dollars, e.g. 1.50 or -2 (or price_delta_cents)
    kitchen_name    optional, any script
    min, max        optional, how many the customer must / may choose from the group
    available_from  optional group window in local time, e.g. 11:00
    available_to    optional, e.g. 15:00 (before available_from = past midnight)
    items           optional, the menu items the group applies to, separated by ;

Group settings (min, max, the window, items) can sit on any row of the group, but must
agree wherever they are repeated. Groups and modifiers match existing ones by name
(ignoring case): matches are updated with whatever changed, new names are created, and the
group is attached to every listed item; nothing is removed or detached, so a sheet can be
re-imported after a change. Every change goes through the menu service, so it lands in the
change history and can be reverted. The whole file is checked first; if anything is wrong,
nothing is applied and every problem is reported.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal, InvalidOperation
from io import StringIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import InvalidRequest
from ..models import MenuItemModifierGroup, Modifier, ModifierGroup
from .history import group_values, modifier_values
from .importer import ImportResult, _text
from .service import (
    attach_modifier_group,
    create_modifier,
    create_modifier_group,
    list_items,
    list_modifier_groups,
    update_modifier,
    update_modifier_group,
)

MAX_DELTA_CENTS = 10_000_000
_GROUP_SETTINGS = ("min_select", "max_select", "available_from", "available_to")


@dataclass(frozen=True)
class ModifierRow:
    line: int
    name: str
    price_delta_cents: int
    kitchen_name: str | None

    def fields(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "price_delta_cents": self.price_delta_cents,
            "kitchen_name": self.kitchen_name,
        }


@dataclass
class GroupRows:
    line: int
    name: str
    settings: dict[str, Any] = field(default_factory=dict)  # only what the sheet gives
    modifiers: list[ModifierRow] = field(default_factory=list)
    item_names: list[str] = field(default_factory=list)


def _delta_cents(row: dict[str, str]) -> int:
    if (row.get("price") or "").strip():
        raw = row["price"].strip().replace("$", "").replace(",", "")
        try:
            cents = Decimal(raw) * 100
        except InvalidOperation as exc:
            raise ValueError(f"price {row['price']!r} isn't a number") from exc
        if cents != cents.to_integral_value():
            raise ValueError(f"price {row['price']!r} has fractions of a cent")
        value = int(cents)
    elif (row.get("price_delta_cents") or "").strip():
        try:
            value = int(row["price_delta_cents"].strip())
        except ValueError as exc:
            raise ValueError(
                f"price_delta_cents {row['price_delta_cents']!r} isn't a whole number"
            ) from exc
    else:
        value = 0
    if not -MAX_DELTA_CENTS <= value <= MAX_DELTA_CENTS:
        raise ValueError("price must be between -$100,000 and $100,000")
    return value


def _count(row: dict[str, str], key: str) -> int | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} {raw!r} isn't a whole number") from exc
    if not 0 <= value <= 100:
        raise ValueError(f"{key} must be between 0 and 100")
    return value


def _clock(row: dict[str, str], key: str) -> time | None:
    raw = (row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return time.fromisoformat(raw if len(raw) > 4 else raw.zfill(5))
    except ValueError as exc:
        raise ValueError(f"{key} {raw!r} isn't a time like 11:30") from exc


def _settings(row: dict[str, str]) -> dict[str, Any]:
    given = {
        "min_select": _count(row, "min"),
        "max_select": _count(row, "max"),
        "available_from": _clock(row, "available_from"),
        "available_to": _clock(row, "available_to"),
    }
    return {key: value for key, value in given.items() if value is not None}


def parse_modifier_csv(text: str) -> list[GroupRows]:
    """Every group with its modifiers, or an InvalidRequest listing each problem by
    spreadsheet row number."""
    reader = csv.DictReader(StringIO(text.lstrip("﻿")))
    if reader.fieldnames is None:
        raise InvalidRequest("the file is empty", code="invalid_menu_file")
    reader.fieldnames = [(name or "").strip().lower() for name in reader.fieldnames]
    if not {"group", "modifier"} <= set(reader.fieldnames):
        raise InvalidRequest(
            "the header row needs a group column and a modifier column",
            code="invalid_menu_file",
        )

    groups: dict[str, GroupRows] = {}
    where: dict[tuple[str, str], int] = {}  # (group, setting or modifier) -> first row
    problems: list[str] = []
    for line, raw in enumerate(reader, start=2):  # row 1 is the header
        if not any((value or "").strip() for value in raw.values()):
            continue
        try:
            group_name = _text(raw, "group", 200)
            name = _text(raw, "modifier", 200)
            if group_name is None or name is None:
                raise ValueError("group and modifier are both needed")
            key = group_name.casefold()
            group = groups.setdefault(key, GroupRows(line=line, name=group_name))
            if (key, "~" + name.casefold()) in where:
                raise ValueError(
                    f"{name!r} is already in {group.name!r} on row {where[key, '~' + name.casefold()]}"
                )
            settings = _settings(raw)
            for setting, value in settings.items():
                if setting in group.settings and group.settings[setting] != value:
                    raise ValueError(
                        f"{setting.replace('_select', '')} for {group.name!r} is "
                        f"{group.settings[setting]} on row {where[key, setting]}, not {value}"
                    )
                where.setdefault((key, setting), line)
            modifier = ModifierRow(
                line=line,
                name=name,
                price_delta_cents=_delta_cents(raw),
                kitchen_name=_text(raw, "kitchen_name", 200),
            )
            group.settings.update(settings)
            where[key, "~" + name.casefold()] = line
            group.modifiers.append(modifier)
            for item in (raw.get("items") or "").split(";"):
                item = item.strip()
                if item and item.casefold() not in {n.casefold() for n in group.item_names}:
                    group.item_names.append(item)
        except ValueError as exc:
            problems.append(f"row {line}: {exc}")
    for group in groups.values():
        low, high = group.settings.get("min_select"), group.settings.get("max_select")
        if low is not None and high is not None and high < low:
            problems.append(f"row {group.line}: max for {group.name!r} is less than its min")
        start, end = group.settings.get("available_from"), group.settings.get("available_to")
        if start is not None and start == end:
            problems.append(
                f"row {group.line}: {group.name!r} starts and ends at the same time"
            )
    if problems:
        raise InvalidRequest("; ".join(problems), code="invalid_menu_file", problems=problems)
    if not groups:
        raise InvalidRequest("the file has no modifiers", code="invalid_menu_file")
    return list(groups.values())


@dataclass
class ModifierImportResult(ImportResult):
    attached: list[str] = field(default_factory=list)


def _recorded(value: Any) -> Any:
    return value.isoformat() if isinstance(value, time) else value


def import_modifiers(
    session: Session, location_id: str, groups: list[GroupRows], *, dry_run: bool = False
) -> ModifierImportResult:
    """Create or update each group and modifier at the location and attach groups to
    their items. With ``dry_run`` nothing is written; the result says what would happen."""
    items = {item.name.casefold(): item for item in list_items(session, location_id)}
    missing = [
        f"row {group.line}: {group.name!r} lists {name!r}, which isn't on the menu"
        for group in groups
        for name in group.item_names
        if name.casefold() not in items
    ]
    if missing:
        raise InvalidRequest("; ".join(missing), code="unknown_menu_item", problems=missing)

    existing = {g.name.casefold(): g for g in list_modifier_groups(session, location_id)}
    result = ModifierImportResult()
    for rows in groups:
        wanted = {"name": rows.name, **rows.settings}
        group = existing.get(rows.name.casefold())
        if group is None:
            result.created.append(rows.name)
            if not dry_run:
                group = create_modifier_group(session, location_id=location_id, **wanted)
        else:
            current = group_values(group)
            changes = {k: v for k, v in wanted.items() if current[k] != _recorded(v)}
            if changes:
                if not dry_run:
                    update_modifier_group(session, group.id, changes)
                result.updated.append(rows.name)
            else:
                result.unchanged.append(rows.name)
        _import_group_modifiers(session, result, rows, group, dry_run)
        for item_name in rows.item_names:
            item = items[item_name.casefold()]
            link = None if group is None else (item.id, group.id)
            if link is not None and session.get(MenuItemModifierGroup, link) is not None:
                continue
            result.attached.append(f"{rows.name} -> {item.name}")
            if group is not None and not dry_run:
                attach_modifier_group(session, menu_item_id=item.id, modifier_group_id=group.id)
    return result


def _import_group_modifiers(
    session: Session,
    result: ModifierImportResult,
    rows: GroupRows,
    group: ModifierGroup | None,
    dry_run: bool,
) -> None:
    existing: dict[str, Modifier] = {}
    if group is not None:
        existing = {
            m.name.casefold(): m
            for m in session.scalars(select(Modifier).where(Modifier.group_id == group.id))
        }
    for row in rows.modifiers:
        label = f"{rows.name}: {row.name}"
        modifier = existing.get(row.name.casefold())
        if modifier is None:
            result.created.append(label)
            if group is not None and not dry_run:
                create_modifier(session, group_id=group.id, **row.fields())
            continue
        current = modifier_values(modifier)
        changes = {k: v for k, v in row.fields().items() if current[k] != v}
        if not changes:
            result.unchanged.append(label)
            continue
        if not dry_run:
            update_modifier(session, modifier.id, changes)
        result.updated.append(label)
