"""Full data export — the anti-lock-in promise: an owner can take everything out.

``export_location`` returns a complete, JSON-serializable dump (menu, customers with
loyalty balances, orders with their lines and payments, and the loyalty ledger).
``export_csv`` renders a single entity as CSV for spreadsheets.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import UTC, datetime
from io import StringIO
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import NotFound
from ..models import (
    Customer,
    Location,
    LoyaltyLedger,
    MenuChange,
    MenuItem,
    Modifier,
    ModifierGroup,
    Order,
    OrderLine,
    Payment,
)

CSV_ENTITIES = ("menu_items", "orders", "order_lines", "payments", "customers", "loyalty_ledger")


def export_location(session: Session, location_id: str) -> dict[str, Any]:
    loc = session.get(Location, location_id)
    if loc is None:
        raise NotFound("location not found")

    items = session.scalars(select(MenuItem).where(MenuItem.location_id == location_id)).all()
    groups = session.scalars(
        select(ModifierGroup).where(ModifierGroup.location_id == location_id)
    ).all()
    group_ids = [g.id for g in groups]
    modifiers = (
        session.scalars(select(Modifier).where(Modifier.group_id.in_(group_ids))).all()
        if group_ids
        else []
    )
    orders = session.scalars(select(Order).where(Order.location_id == location_id)).all()
    order_ids = [o.id for o in orders]
    lines = (
        session.scalars(select(OrderLine).where(OrderLine.order_id.in_(order_ids))).all()
        if order_ids
        else []
    )
    payments = session.scalars(select(Payment).where(Payment.location_id == location_id)).all()
    ledger = session.scalars(
        select(LoyaltyLedger).where(LoyaltyLedger.location_id == location_id)
    ).all()
    customers = session.scalars(select(Customer).where(Customer.location_id == location_id)).all()
    changes = session.scalars(
        select(MenuChange)
        .where(MenuChange.location_id == location_id)
        .order_by(MenuChange.number)
    ).all()

    balances: dict[str, int] = defaultdict(int)
    for entry in ledger:
        balances[entry.customer_id] += entry.points_delta

    lines_by_order: dict[str, list[OrderLine]] = defaultdict(list)
    for line in lines:
        lines_by_order[line.order_id].append(line)
    payments_by_order: dict[str, list[Payment]] = defaultdict(list)
    for payment in payments:
        payments_by_order[payment.order_id].append(payment)

    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "location": {
            "id": loc.id,
            "org_id": loc.org_id,
            "name": loc.name,
            "timezone": loc.timezone,
        },
        "menu": {
            "items": [_menu_item(i) for i in items],
            "modifier_groups": [_modifier_group(g) for g in groups],
            "modifiers": [_modifier(m) for m in modifiers],
        },
        "customers": [
            {**_customer(c), "loyalty_balance": balances.get(c.id, 0)} for c in customers
        ],
        "orders": [
            {
                **_order(o),
                "lines": [_order_line(line) for line in lines_by_order.get(o.id, [])],
                "payments": [_payment(p) for p in payments_by_order.get(o.id, [])],
            }
            for o in orders
        ],
        "loyalty_ledger": [_ledger_entry(e) for e in ledger],
        "menu_changes": [_menu_change(c) for c in changes],
    }


def export_csv(session: Session, location_id: str, entity: str) -> str:
    if entity not in CSV_ENTITIES:
        raise NotFound(
            f"unknown export {entity!r}; one of {', '.join(CSV_ENTITIES)}", code="unknown_entity"
        )
    bundle = export_location(session, location_id)
    rows, header = _csv_rows(bundle, entity)
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def _csv_rows(bundle: dict[str, Any], entity: str) -> tuple[list[dict[str, Any]], list[str]]:
    if entity == "menu_items":
        return bundle["menu"]["items"], [
            "id", "name", "kitchen_name", "category", "base_price_cents", "tax_rate", "is_available"
        ]
    if entity == "orders":
        rows = [{k: v for k, v in o.items() if k not in ("lines", "payments")} for o in bundle["orders"]]
        return rows, [
            "id", "order_number", "business_date", "channel", "fulfillment", "state",
            "customer_id", "device_id"
        ]
    if entity == "order_lines":
        rows = [{"order_id": o["id"], **line} for o in bundle["orders"] for line in o["lines"]]
        return rows, [
            "order_id", "id", "parent_line_id", "menu_item_id", "modifier_id", "quantity",
            "name", "unit_price_cents", "tax_rate", "category", "seat_number", "course_number"
        ]
    if entity == "payments":
        rows = [{"order_id": o["id"], **p} for o in bundle["orders"] for p in o["payments"]]
        return rows, [
            "order_id", "id", "provider", "external_ref", "amount_cents", "currency", "status",
            "business_date"
        ]
    if entity == "customers":
        return bundle["customers"], ["id", "name", "phone", "email", "loyalty_balance"]
    # loyalty_ledger
    return bundle["loyalty_ledger"], ["id", "customer_id", "order_id", "points_delta", "reason"]


def _menu_item(i: MenuItem) -> dict[str, Any]:
    return {
        "id": i.id,
        "name": i.name,
        "kitchen_name": i.kitchen_name,
        "category": i.category,
        "base_price_cents": i.base_price_cents,
        "tax_rate": float(i.tax_rate),
        "is_available": i.is_available,
    }


def _modifier_group(g: ModifierGroup) -> dict[str, Any]:
    return {"id": g.id, "name": g.name, "min_select": g.min_select, "max_select": g.max_select}


def _modifier(m: Modifier) -> dict[str, Any]:
    return {
        "id": m.id,
        "group_id": m.group_id,
        "name": m.name,
        "kitchen_name": m.kitchen_name,
        "price_delta_cents": m.price_delta_cents,
    }


def _customer(c: Customer) -> dict[str, Any]:
    return {"id": c.id, "name": c.name, "phone": c.phone, "email": c.email}


def _order(o: Order) -> dict[str, Any]:
    return {
        "id": o.id,
        "order_number": o.order_number,
        "business_date": o.business_date.isoformat(),
        "channel": o.channel,
        "fulfillment": o.fulfillment,
        "state": o.state,
        "customer_id": o.customer_id,
        "device_id": o.device_id,
    }


def _order_line(line: OrderLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "parent_line_id": line.parent_line_id,
        "menu_item_id": line.menu_item_id,
        "modifier_id": line.modifier_id,
        "quantity": line.quantity,
        "name": line.name_snapshot,
        "unit_price_cents": line.unit_price_cents,
        "tax_rate": float(line.tax_rate_snapshot),
        "category": line.category_snapshot,
        "seat_number": line.seat_number,
        "course_number": line.course_number,
    }


def _payment(p: Payment) -> dict[str, Any]:
    return {
        "id": p.id,
        "provider": p.provider,
        "external_ref": p.external_ref,
        "amount_cents": p.amount_cents,
        "currency": p.currency,
        "status": p.status,
        "business_date": p.business_date.isoformat(),
    }


def _ledger_entry(e: LoyaltyLedger) -> dict[str, Any]:
    return {
        "id": e.id,
        "customer_id": e.customer_id,
        "order_id": e.order_id,
        "points_delta": e.points_delta,
        "reason": e.reason,
    }


def _menu_change(c: MenuChange) -> dict[str, Any]:
    return {
        "number": c.number,
        "entity": c.entity,
        "entity_id": c.entity_id,
        "action": c.action,
        "before": c.before,
        "after": c.after,
        "reverts_id": c.reverts_id,
        "actor": c.actor,
        "changed_at": c.created_at.isoformat(),
    }
