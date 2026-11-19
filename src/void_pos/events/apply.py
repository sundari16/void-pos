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
from datetime import UTC, date, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..balance import (
    active_adjustments,
    active_lines,
    balance_cents,
    captured_cents,
    refunded_cents,
)
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
    OrderAdjustment,
    OrderLine,
    Payment,
    Refund,
    Staff,
)
from ..staff import managers_required
from ..totals import Adjustment, order_math
from .payloads import MAX_ORDER_LINES, PAYLOAD_SCHEMAS
from .types import (
    OPEN,
    PAID,
    REFUNDED,
    SENT,
    UNSETTLED,
    VOIDED,
    EventType,
    can_transition,
)


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
    elif event_type in _ORDER_EVENTS:
        order = session.get(Order, payload["order_id"])
        if order is not None and order.location_id != location_id:
            return reject("foreign_order", "the order belongs to another location")
        if event_type in _CHANGES and order is not None:
            problem = _change_problem(session, order, event_type, payload)
            if problem is not None:
                return reject(*problem)
        if event_type in _CHANGES and order is None:
            return reject("unknown_order", "the order isn't here yet; send its order.placed first")
        if event_type == EventType.ORDER_LINES_ADDED:
            if order is None:
                return reject(
                    "unknown_order", "the order isn't here yet; send its order.placed first"
                )
            ids = [line["id"] for line in payload["lines"]]
            taken = session.scalar(select(OrderLine.id).where(OrderLine.id.in_(ids)).limit(1))
            if taken is not None:
                return reject("line_exists", f"order line {taken} already exists")
            count = session.scalar(
                select(func.count()).select_from(OrderLine).where(OrderLine.order_id == order.id)
            )
            if (count or 0) + len(ids) > MAX_ORDER_LINES:
                return reject(
                    "order_too_long",
                    f"an order can have at most {MAX_ORDER_LINES} lines; start another",
                )
        if event_type == EventType.ORDER_VOIDED:
            refusal = _approval_refusal(session, location_id, payload)
            if refusal is not None:
                return reject("manager_approval_required", refusal)
    elif event_type == EventType.LOYALTY_RECORDED:
        if _local_customer(session, payload["customer_id"], location_id) is None:
            return reject("unknown_customer", "the customer isn't known at this venue")
        order_id = payload.get("order_id")
        order = session.get(Order, order_id) if order_id else None
        if order is not None and order.location_id != location_id:
            return reject("foreign_order", "the order belongs to another location")
    return None


_CHANGES = (
    EventType.ORDER_LINES_REMOVED,
    EventType.ORDER_ADJUSTED,
    EventType.ORDER_ADJUSTMENT_REMOVED,
    EventType.ORDER_REFUNDED,
)
_ORDER_EVENTS = (
    EventType.ORDER_SENT,
    EventType.ORDER_VOIDED,
    EventType.ORDER_PAID,
    EventType.ORDER_LINES_ADDED,
    *_CHANGES,
)


def _change_problem(
    session: Session, order: Order, event_type: str, payload: dict[str, Any]
) -> tuple[str, str] | None:
    """Why an item removal, adjustment or refund can't be recorded, as (code, detail)."""
    location_id = order.location_id
    lines = active_lines(session, order.id)
    adjustments: list[Adjustment] = list(active_adjustments(session, order.id))
    needs_approval = False

    if event_type == EventType.ORDER_LINES_REMOVED:
        by_id = {line.id: line for line in lines}
        for line_id in payload["line_ids"]:
            line = by_id.get(line_id)
            if line is None or line.parent_line_id is not None:
                return "unknown_line", f"line {line_id} isn't an item on this order"
        gone = set(payload["line_ids"])
        lines = [x for x in lines if x.id not in gone and x.parent_line_id not in gone]
        needs_approval = any(by_id[i].sent_at is not None for i in gone)
    elif event_type == EventType.ORDER_ADJUSTED:
        adj = payload["adjustment"]
        if session.get(OrderAdjustment, adj["id"]) is not None:
            return "adjustment_exists", "an adjustment with this id already exists"
        adjustments = [*adjustments, _NewAdjustment(**{k: adj[k] for k in _ADJ_KEYS})]
        needs_approval = adj["kind"] == "discount"
    elif event_type == EventType.ORDER_ADJUSTMENT_REMOVED:
        before = len(adjustments)
        adjustments = [a for a in adjustments if a.id != payload["adjustment_id"]]
        if len(adjustments) == before:
            return "unknown_adjustment", "that adjustment isn't on this order"
    else:  # refund
        refund = payload["refund"]
        if session.get(Refund, refund["id"]) is not None:
            return "refund_exists", "a refund with this id already exists"
        if order.state != PAID:
            return "order_not_paid", "only a paid order can be refunded"
        payment = session.get(Payment, refund["payment_id"])
        if payment is None or payment.order_id != order.id or payment.status != "captured":
            return "unknown_payment", "that payment isn't a captured payment on this order"
        if refund["provider"] != payment.provider:
            return "wrong_refund_method", f"refund a {payment.provider} payment the same way"
        given = session.scalar(
            select(func.coalesce(func.sum(Refund.amount_cents), 0)).where(
                Refund.payment_id == payment.id
            )
        )
        left = payment.amount_cents + payment.tip_cents - int(given or 0)
        if refund["amount_cents"] > left:
            return "refund_exceeds_payment", f"only {left} cents of that payment is left to refund"
        needs_approval = True

    if event_type != EventType.ORDER_REFUNDED:
        new_total = order_math(lines, adjustments).total_cents
        if new_total < captured_cents(session, order.id):
            return "below_paid", "that would take the order below what's already been paid"
    if needs_approval:
        refusal = _approval_refusal(session, location_id, payload)
        if refusal is not None:
            return "manager_approval_required", refusal
    return None


_ADJ_KEYS = ("id", "kind", "basis", "value")


@dataclass(frozen=True)
class _NewAdjustment:
    id: str
    kind: str
    basis: str
    value: int


def _approval_refusal(
    session: Session, location_id: str, payload: dict[str, Any]
) -> str | None:
    """Once a location has a manager, a change that needs approval (a void, a refund, a
    discount, taking off an item the kitchen has) must name an active manager there, by
    id and name, whichever path it came by. The online path names the manager whose PIN
    it checked; an event from sync can't name one it doesn't know."""
    if not managers_required(session, location_id):
        return None
    manager = session.get(Staff, payload.get("approved_by") or "")
    if (
        manager is None
        or manager.location_id != location_id
        or manager.role != "manager"
        or not manager.active
        or manager.name != payload.get("approved_by_name")
    ):
        return "this needs a manager's approval"
    return None


# --- projection handlers ---------------------------------------------------------


def _utc(at: datetime) -> datetime:
    """Stored times are UTC (SQLite keeps the wall time and drops the zone)."""
    return as_aware(at).astimezone(UTC)


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
            placed_at=_utc(event.occurred_at),
            state="open",
        )
    )
    _add_lines(session, event, order_id, p.get("lines", []))
    return ApplyResult(event.event_id, "applied")


def _add_lines(
    session: Session, event: Event, order_id: str, lines: list[dict[str, Any]]
) -> None:
    for line in lines:
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


def _h_order_lines_added(session: Session, event: Event) -> ApplyResult:
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")
    if order.state not in UNSETTLED:
        return ApplyResult(event.event_id, "conflict", f"can't add to a {order.state} order")
    _add_lines(session, event, order.id, event.payload["lines"])
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
    """Mark the lines the kitchen got, then move an open order to sent. Sending more
    lines to an order the kitchen already has (items added) is applied, not a duplicate;
    so is a counter-service order sent after it was paid."""
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")
    if order.state == VOIDED:
        return ApplyResult(event.event_id, "conflict", "voided -> sent not allowed")
    stmt = select(OrderLine).where(
        OrderLine.order_id == order.id,
        OrderLine.sent_at.is_(None),
        OrderLine.removed_at.is_(None),
    )
    line_ids = event.payload.get("line_ids")
    if line_ids is not None:
        stmt = stmt.where(OrderLine.id.in_(line_ids))
    marked = 0
    for line in session.scalars(stmt):
        line.sent_at = _utc(event.occurred_at)
        marked += 1
    if order.state == OPEN:
        order.state = SENT
        return ApplyResult(event.event_id, "applied")
    if marked:
        return ApplyResult(event.event_id, "applied", f"{marked} more lines to the kitchen")
    return ApplyResult(event.event_id, "duplicate", f"already {order.state}")


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
                tip_cents=pay.get("tip_cents", 0),
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


def _unsettled_order(session: Session, event: Event) -> Order | ApplyResult:
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")
    if order.state not in UNSETTLED:
        return ApplyResult(event.event_id, "conflict", f"can't change a {order.state} order")
    return order


def _settled(session: Session, event: Event, order: Order) -> ApplyResult:
    """After taking items off or changing adjustments: if what's been paid now covers the
    order, it's paid (a $0 order with nothing paid stays open, to add to or void)."""
    session.flush()
    if captured_cents(session, order.id) > 0 and balance_cents(session, order.id) == 0:
        order.state = PAID
        return ApplyResult(event.event_id, "applied", "now paid in full")
    return ApplyResult(event.event_id, "applied")


def _h_order_lines_removed(session: Session, event: Event) -> ApplyResult:
    order = _unsettled_order(session, event)
    if isinstance(order, ApplyResult):
        return order
    gone = set(event.payload["line_ids"])
    for line in session.scalars(
        select(OrderLine).where(
            OrderLine.order_id == order.id,
            OrderLine.removed_at.is_(None),
            (OrderLine.id.in_(gone)) | (OrderLine.parent_line_id.in_(gone)),
        )
    ):
        line.removed_at = _utc(event.occurred_at)
    return _settled(session, event, order)


def _h_order_adjusted(session: Session, event: Event) -> ApplyResult:
    order = _unsettled_order(session, event)
    if isinstance(order, ApplyResult):
        return order
    adj = event.payload["adjustment"]
    session.add(
        OrderAdjustment(
            id=adj["id"],
            order_id=order.id,
            kind=adj["kind"],
            basis=adj["basis"],
            value=adj["value"],
            label=adj.get("label"),
            approved_by=event.payload.get("approved_by"),
        )
    )
    return _settled(session, event, order)


def _h_order_adjustment_removed(session: Session, event: Event) -> ApplyResult:
    order = _unsettled_order(session, event)
    if isinstance(order, ApplyResult):
        return order
    adj = session.get(OrderAdjustment, event.payload["adjustment_id"])
    if adj is None or adj.removed_at is not None:
        return ApplyResult(event.event_id, "duplicate", "adjustment already removed")
    adj.removed_at = _utc(event.occurred_at)
    return _settled(session, event, order)


def _h_order_refunded(session: Session, event: Event) -> ApplyResult:
    order = session.get(Order, event.payload["order_id"])
    if order is None:
        return ApplyResult(event.event_id, "ignored", "order not found")
    r = event.payload["refund"]
    session.add(
        Refund(
            id=r["id"],
            order_id=order.id,
            payment_id=r["payment_id"],
            location_id=event.location_id,
            business_date=event.business_date,
            amount_cents=r["amount_cents"],
            reason=r["reason"],
            provider=r["provider"],
            external_ref=r.get("external_ref"),
            approved_by=event.payload.get("approved_by"),
        )
    )
    session.flush()
    taken = session.scalar(
        select(func.coalesce(func.sum(Payment.amount_cents + Payment.tip_cents), 0)).where(
            Payment.order_id == order.id, Payment.status == "captured"
        )
    )
    if order.state == PAID and refunded_cents(session, order.id) >= int(taken or 0):
        order.state = REFUNDED
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
    EventType.ORDER_LINES_ADDED: _h_order_lines_added,
    EventType.ORDER_LINES_REMOVED: _h_order_lines_removed,
    EventType.ORDER_ADJUSTED: _h_order_adjusted,
    EventType.ORDER_ADJUSTMENT_REMOVED: _h_order_adjustment_removed,
    EventType.ORDER_REFUNDED: _h_order_refunded,
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
