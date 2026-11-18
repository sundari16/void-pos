"""Fold the append-only event log onto the projected aggregates.

Two guarantees make offline-first sync safe:

* **Idempotent** — an event already in the log (same ``event_id``) is a no-op, so a
  device re-sending after a reconnect changes nothing.
* **Deterministic** — ``apply_events`` sorts a batch into one canonical order
  ``(occurred_at, event_id)`` before applying, so events produced on different
  offline devices resolve to the same result on every node, regardless of the order
  they arrive in. Conflicting order-state transitions are settled by the order state
  machine (``types.can_transition``): the first terminal transition in that canonical
  order wins; a later one is recorded as a conflict rather than overwriting it.
* **Validated** — an event from an unknown device, for another location (or touching
  another location's order), reusing a seq or an order number, or with a malformed
  payload is ``rejected`` and never reaches the log, so one bad event in an offline
  batch can't take the rest of the batch down with it. A reference to a menu item,
  modifier or customer that doesn't exist here (deleted, or sold from a stale menu) is
  dropped from the projection: the line's snapshot already carries what was sold.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..balance import balance_cents
from ..clock import as_aware
from ..models import (
    Customer,
    Device,
    Event,
    Location,
    LoyaltyLedger,
    MenuItem,
    Modifier,
    ModifierGroup,
    Order,
    OrderLine,
    Payment,
)
from .payloads import PAYLOAD_SCHEMAS
from .types import PAID, SENT, VOIDED, EventType, can_transition


@dataclass
class ApplyResult:
    event_id: str
    status: str  # applied | duplicate | conflict | ignored | rejected
    detail: str | None = None
    code: str | None = None  # set on rejected: unknown_device, seq_conflict, ...


def apply_events(session: Session, incoming: list[dict[str, Any]]) -> list[ApplyResult]:
    """Apply a batch of events in canonical (occurred_at, event_id) order."""
    ordered = sorted(incoming, key=lambda e: (_as_dt(e["occurred_at"]), e["event_id"]))
    return [ingest_event(session, e) for e in ordered]


def ingest_event(session: Session, incoming: dict[str, Any]) -> ApplyResult:
    """Append one event to the log (idempotently) and project it onto the aggregates."""
    event_id = incoming["event_id"]
    if session.get(Event, event_id) is not None:
        return ApplyResult(event_id, "duplicate", "event already in the log")

    rejected = _validate(session, incoming)
    if rejected is not None:
        return rejected

    event = Event(
        event_id=event_id,
        device_id=incoming["device_id"],
        seq=incoming["seq"],
        location_id=incoming["location_id"],
        business_date=_as_date(incoming["business_date"]),
        occurred_at=_as_dt(incoming["occurred_at"]),
        type=incoming["type"],
        payload=incoming.get("payload", {}),
    )
    session.add(event)

    result = _project(session, event)
    _advance_device_seq(session, event)
    session.flush()
    return result


# --- validation --------------------------------------------------------------------


def _validate(session: Session, incoming: dict[str, Any]) -> ApplyResult | None:
    event_id = incoming["event_id"]

    def reject(code: str, detail: str) -> ApplyResult:
        return ApplyResult(event_id, "rejected", detail, code)

    device = session.get(Device, incoming["device_id"])
    if device is None:
        return reject("unknown_device", "the event's device is not registered")
    if device.location_id != incoming["location_id"]:
        return reject("location_mismatch", "the event's location is not the device's location")

    seq = incoming["seq"]
    taken = session.scalar(
        select(Event.event_id).where(Event.device_id == device.id, Event.seq == seq).limit(1)
    )
    if taken is not None:
        return reject("seq_conflict", f"seq {seq} is already used by this device")

    schema = PAYLOAD_SCHEMAS.get(incoming["type"])
    if schema is not None:
        try:
            schema.model_validate(incoming.get("payload", {}))
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"])
            detail = f"invalid {incoming['type']} payload: {where + ': ' if where else ''}"
            return reject("invalid_payload", detail + first["msg"])

    payload = incoming.get("payload", {})
    location_id = incoming["location_id"]
    event_type = incoming["type"]
    if event_type == EventType.ORDER_PLACED:
        number = payload["order_number"]
        if not device.order_number_start <= number <= device.order_number_end:
            return reject(
                "order_number_out_of_range",
                f"order number {number} is outside this device's range "
                f"{device.order_number_start}-{device.order_number_end}",
            )
        if session.get(Order, payload["order_id"]) is not None:
            return reject("order_exists", "an order with this id already exists")
        used = session.scalar(
            select(Order.id)
            .where(Order.device_id == device.id, Order.order_number == number)
            .limit(1)
        )
        if used is not None:
            return reject(
                "order_number_used", f"order number {number} is already used by this device"
            )
    elif event_type in (EventType.ORDER_SENT, EventType.ORDER_VOIDED, EventType.ORDER_PAID):
        order = session.get(Order, payload["order_id"])
        if order is not None and order.location_id != location_id:
            return reject("foreign_order", "the order belongs to another location")
    elif event_type == EventType.LOYALTY_RECORDED:
        if _local_customer(session, payload["customer_id"], location_id) is None:
            return reject("unknown_customer", "the customer isn't known at this venue")
        order_id = payload.get("order_id")
        order = session.get(Order, order_id) if order_id else None
        if order is not None and order.location_id != location_id:
            return reject("foreign_order", "the order belongs to another location")
    return None


# --- projection handlers ---------------------------------------------------------


def _project(session: Session, event: Event) -> ApplyResult:
    handler = _HANDLERS.get(event.type)
    if handler is None:
        return ApplyResult(event.event_id, "ignored", f"no handler for {event.type!r}")
    return handler(session, event)


def _h_order_placed(session: Session, event: Event) -> ApplyResult:
    p = event.payload
    order_id = p["order_id"]
    if session.get(Order, order_id) is not None:
        return ApplyResult(event.event_id, "duplicate", "order already projected")

    session.add(
        Order(
            id=order_id,
            location_id=event.location_id,
            device_id=event.device_id,
            customer_id=_local_customer(session, p.get("customer_id"), event.location_id),
            order_number=p["order_number"],
            business_date=event.business_date,
            channel=p["channel"],
            fulfillment=p["fulfillment"],
            table_label=p.get("table_label"),
            state="open",
        )
    )
    for line in p.get("lines", []):
        session.add(
            OrderLine(
                id=line["id"],
                order_id=order_id,
                parent_line_id=line.get("parent_line_id"),
                menu_item_id=_local_menu_item(session, line.get("menu_item_id"), event.location_id),
                modifier_id=_local_modifier(session, line.get("modifier_id"), event.location_id),
                quantity=line.get("quantity", 1),
                name_snapshot=line["name_snapshot"],
                kitchen_name_snapshot=line.get("kitchen_name_snapshot"),
                unit_price_cents=line["unit_price_cents"],
                tax_rate_snapshot=line.get("tax_rate_snapshot", 0),
                category_snapshot=line.get("category_snapshot"),
                seat_number=line.get("seat_number"),
                course_number=line.get("course_number"),
            )
        )
    return ApplyResult(event.event_id, "applied")


def _transition(session: Session, event: Event, target: str) -> ApplyResult:
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")
    if order.state == target:
        return ApplyResult(event.event_id, "duplicate", f"already {target}")
    if not can_transition(order.state, target):
        return ApplyResult(event.event_id, "conflict", f"{order.state} -> {target} not allowed")
    order.state = target
    return ApplyResult(event.event_id, "applied")


def _h_order_sent(session: Session, event: Event) -> ApplyResult:
    return _transition(session, event, SENT)


def _h_order_voided(session: Session, event: Event) -> ApplyResult:
    return _transition(session, event, VOIDED)


def _h_order_paid(session: Session, event: Event) -> ApplyResult:
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")

    # Money moved: record the payment even if the state transition is a conflict, so a
    # payment against an already-voided order surfaces for reconciliation.
    pay = event.payload.get("payment")
    if pay is not None and session.get(Payment, pay["id"]) is None:
        session.add(
            Payment(
                id=pay["id"],
                order_id=order.id,
                location_id=event.location_id,
                business_date=event.business_date,
                provider=pay["provider"],
                external_ref=pay.get("external_ref"),
                amount_cents=pay["amount_cents"],
                currency=pay.get("currency", "AUD"),
                status=pay.get("status", "captured"),
            )
        )

    if order.state == PAID:
        return ApplyResult(event.event_id, "duplicate", "already paid")
    if not can_transition(order.state, PAID):
        return ApplyResult(event.event_id, "conflict", f"{order.state} -> paid not allowed")
    if pay is not None:
        session.flush()
        owing = balance_cents(session, order.id)
        if owing > 0:
            return ApplyResult(event.event_id, "applied", f"part payment; {owing} cents owing")
    order.state = PAID
    return ApplyResult(event.event_id, "applied")


def _h_loyalty_recorded(session: Session, event: Event) -> ApplyResult:
    p = event.payload
    ledger_id = p["id"]
    if session.get(LoyaltyLedger, ledger_id) is not None:
        return ApplyResult(event.event_id, "duplicate", "ledger entry already recorded")
    session.add(
        LoyaltyLedger(
            id=ledger_id,
            location_id=event.location_id,
            customer_id=p["customer_id"],
            order_id=p.get("order_id") if session.get(Order, p.get("order_id") or "") else None,
            points_delta=p["points_delta"],
            reason=p["reason"],
        )
    )
    return ApplyResult(event.event_id, "applied")


_HANDLERS: dict[str, Callable[[Session, Event], ApplyResult]] = {
    EventType.ORDER_PLACED: _h_order_placed,
    EventType.ORDER_SENT: _h_order_sent,
    EventType.ORDER_PAID: _h_order_paid,
    EventType.ORDER_VOIDED: _h_order_voided,
    EventType.LOYALTY_RECORDED: _h_loyalty_recorded,
}


# --- helpers ---------------------------------------------------------------------


def _advance_device_seq(session: Session, event: Event) -> None:
    """One conditional UPDATE, so two commands from the same till committing in either
    order can never move its counter backwards."""
    session.execute(
        update(Device)
        .where(Device.id == event.device_id, Device.last_seq < event.seq)
        .values(last_seq=event.seq)
        .execution_options(synchronize_session=False)
    )
    device = session.get(Device, event.device_id)
    if device is not None:
        session.expire(device, ["last_seq"])


def _local_menu_item(session: Session, item_id: str | None, location_id: str) -> str | None:
    if item_id is None:
        return None
    item = session.get(MenuItem, item_id)
    return item_id if item is not None and item.location_id == location_id else None


def _local_modifier(session: Session, modifier_id: str | None, location_id: str) -> str | None:
    if modifier_id is None:
        return None
    modifier = session.get(Modifier, modifier_id)
    group = session.get(ModifierGroup, modifier.group_id) if modifier is not None else None
    return modifier_id if group is not None and group.location_id == location_id else None


def _local_customer(session: Session, customer_id: str | None, location_id: str) -> str | None:
    """Customers belong to the org, so any of its locations may reference them."""
    if customer_id is None:
        return None
    customer = session.get(Customer, customer_id)
    location = session.get(Location, location_id)
    if customer is None or location is None or customer.org_id != location.org_id:
        return None
    return customer_id


def _as_date(value: Any) -> date:
    if isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(value, datetime):
        return value.date()
    return value


def _as_dt(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return as_aware(value)
