// A till command as the event the server would record for it, so a command taken while the
// server is out of reach can be queued and pushed later through /sync/push. The payloads are
// exactly what the online routes record (src/void_pos/orders/service.py, payments/service.py),
// and the ids are the ones the till already minted for the request, so a command that did
// reach the server before the connection dropped comes back from the push as a duplicate.
//
// Only changes the till may make on its own are allowed offline. Anything a manager approves
// (a void, a discount, taking off an item the kitchen has, a refund) or that needs the card
// processor (Stripe) waits for the server, which checks the PIN or talks to Stripe.

import { ApiError } from "../lib/api";
import type {
  AddLinesRequest,
  AdjustRequest,
  CommandIn,
  LineIn,
  OrderResponse,
  PayRequest,
  PlaceOrderRequest,
  RemoveLinesRequest,
  SyncEvent,
} from "../lib/types";
import { capturedCents, isUnsettledState, withMoney } from "./projection";
import { orderMath } from "./totals";

export interface TillIdentity {
  deviceId: string;
  locationId: string;
  timezone: string;
}

/** Today's date in the location's timezone, "YYYY-MM-DD" (the server's default). */
export function businessDate(timeZone: string, now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** The day before a "YYYY-MM-DD" date. */
export function dayBefore(date: string): string {
  const d = new Date(`${date}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}

export function needsServer(what: string): ApiError {
  return new ApiError(0, "needs_server", `${what} needs the server; try again once the till is back online.`);
}

function refuse(status: number, code: string, detail: string): never {
  throw new ApiError(status, code, detail);
}

function event(
  till: TillIdentity,
  cmd: CommandIn,
  type: string,
  payload: Record<string, unknown>,
  date: string,
): SyncEvent {
  return {
    event_id: cmd.event_id,
    device_id: till.deviceId,
    seq: cmd.seq,
    location_id: till.locationId,
    business_date: date,
    occurred_at: cmd.occurred_at ?? new Date().toISOString(),
    type,
    payload,
  };
}

/** A line with every field the server's OrderLineIn records, defaults filled in. */
function fullLine(line: LineIn): Record<string, unknown> {
  return {
    id: line.id,
    parent_line_id: line.parent_line_id ?? null,
    menu_item_id: line.menu_item_id ?? null,
    modifier_id: line.modifier_id ?? null,
    quantity: line.quantity ?? 1,
    name_snapshot: line.name_snapshot,
    kitchen_name_snapshot: line.kitchen_name_snapshot ?? null,
    unit_price_cents: line.unit_price_cents,
    tax_rate_snapshot: line.tax_rate_snapshot ?? 0,
    category_snapshot: line.category_snapshot ?? null,
    seat_number: line.seat_number ?? null,
    course_number: line.course_number ?? null,
  };
}

function openOrder(order: OrderResponse): void {
  if (!isUnsettledState(order.state)) refuse(409, "order_closed", `this order is already ${order.state}`);
}

export function placeEvent(till: TillIdentity, body: PlaceOrderRequest): SyncEvent {
  return event(
    till,
    body,
    "order.placed",
    {
      order_id: body.order_id,
      order_number: body.order_number,
      channel: body.channel,
      fulfillment: body.fulfillment,
      customer_id: body.customer_id ?? null,
      table_label: body.table_label ?? null,
      delivery_address: null,
      customer_note: null,
      lines: body.lines.map(fullLine),
    },
    body.business_date ?? businessDate(till.timezone),
  );
}

export function addLinesEvent(till: TillIdentity, order: OrderResponse, body: AddLinesRequest): SyncEvent {
  openOrder(order);
  return event(
    till,
    body,
    "order.lines_added",
    { order_id: order.id, lines: body.lines.map(fullLine) },
    order.business_date,
  );
}

/** Kitchen sends are replayed through the send-to-kitchen route, which prints. */
export function kitchenEvent(till: TillIdentity, order: OrderResponse, body: CommandIn): SyncEvent {
  if (order.state === "voided") refuse(409, "order_voided", "this order is voided");
  const unsent = order.lines.filter((l) => !l.removed && !l.sent).map((l) => l.id);
  return event(till, body, "order.sent", { order_id: order.id, line_ids: unsent }, order.business_date);
}

export function payEvent(till: TillIdentity, order: OrderResponse, body: PayRequest): SyncEvent {
  if (body.method === "stripe") throw needsServer("A card payment through Stripe");
  openOrder(order);
  const owing = order.balance_cents;
  const amount = body.amount_cents ?? owing;
  if (amount > owing) {
    refuse(400, "amount_exceeds_balance", `the payment of ${amount} cents is more than the ${owing} cents owing`);
  }
  if (amount <= 0 && owing > 0) refuse(400, "invalid_amount", "a payment needs an amount above zero");
  return event(
    till,
    body,
    "order.paid",
    {
      order_id: order.id,
      payment: {
        id: body.payment_id,
        provider: body.method,
        external_ref: body.method === "external" ? (body.external_ref ?? null) : null,
        amount_cents: amount,
        tip_cents: body.tip_cents ?? 0,
        currency: body.currency ?? "AUD",
        status: "captured",
      },
    },
    order.business_date,
  );
}

/** Taking items off would drop the total below what's been paid. */
function belowPaid(order: OrderResponse, next: OrderResponse): void {
  if (next.total_cents < capturedCents(order)) {
    refuse(409, "below_paid", "that would take the order below what's already been paid");
  }
}

export function removeLinesEvent(till: TillIdentity, order: OrderResponse, body: RemoveLinesRequest): SyncEvent {
  openOrder(order);
  const lines = new Map(order.lines.map((l) => [l.id, l]));
  for (const id of body.line_ids) {
    const line = lines.get(id);
    if (!line || line.removed || line.parent_line_id !== null) {
      refuse(400, "unknown_line", `line ${id} isn't an item on this order`);
    }
    if (line.sent) throw needsServer("Taking off an item the kitchen has");
  }
  const gone = new Set(body.line_ids);
  belowPaid(
    order,
    withMoney({
      ...order,
      lines: order.lines.map((l) =>
        gone.has(l.id) || (l.parent_line_id !== null && gone.has(l.parent_line_id)) ? { ...l, removed: true } : l,
      ),
    }),
  );
  return event(
    till,
    body,
    "order.lines_removed",
    { order_id: order.id, line_ids: body.line_ids, reason: body.reason },
    order.business_date,
  );
}

export function adjustEvent(till: TillIdentity, order: OrderResponse, body: AdjustRequest): SyncEvent {
  if (body.kind === "discount") throw needsServer("A discount");
  openOrder(order);
  const adjustment = { id: body.id, kind: body.kind, basis: body.basis, value: body.value, label: body.label ?? null };
  const money = orderMath(
    order.lines.filter((l) => !l.removed),
    [...order.adjustments, adjustment],
  );
  if (money.total_cents < capturedCents(order)) {
    refuse(409, "below_paid", "that would take the order below what's already been paid");
  }
  return event(till, body, "order.adjusted", { order_id: order.id, adjustment }, order.business_date);
}

export function removeAdjustmentEvent(
  till: TillIdentity,
  order: OrderResponse,
  adjustmentId: string,
  body: CommandIn,
): SyncEvent {
  openOrder(order);
  if (!order.adjustments.some((a) => a.id === adjustmentId)) {
    refuse(400, "unknown_adjustment", "that adjustment isn't on this order");
  }
  const money = orderMath(
    order.lines.filter((l) => !l.removed),
    order.adjustments.filter((a) => a.id !== adjustmentId),
  );
  if (money.total_cents < capturedCents(order)) {
    refuse(409, "below_paid", "that would take the order below what's already been paid");
  }
  return event(
    till,
    body,
    "order.adjustment_removed",
    { order_id: order.id, adjustment_id: adjustmentId },
    order.business_date,
  );
}
