// Fold sync events onto the till's copy of an order, as the server's apply layer does
// (src/void_pos/events/apply.py), so an order changed offline — on this till, or on another
// one and pulled from /sync/pull — reads the same here as it will on the server.
//
// Every effect is keyed by id (line, payment, adjustment, refund), so applying an event the
// order already reflects changes nothing: a view fetched from the server can have pulled or
// queued events folded onto it safely.

import type {
  AdjustmentOut,
  LineIn,
  OrderLineOut,
  OrderResponse,
  OrderSummary,
  PaymentOut,
  RefundOut,
  SyncEvent,
} from "../lib/types";
import { orderMath } from "./totals";

export type ApplyStatus = "applied" | "duplicate" | "conflict" | "ignored";

export interface Applied {
  order: OrderResponse | null;
  status: ApplyStatus;
  detail?: string;
}

const UNSETTLED = new Set(["open", "sent"]);
const ALLOWED: Record<string, string[]> = {
  open: ["sent", "paid", "voided"],
  sent: ["paid", "voided"],
  paid: ["refunded"],
  voided: [],
  refunded: [],
};

export function canTransition(current: string, target: string): boolean {
  return (ALLOWED[current] ?? []).includes(target);
}

interface PaymentIn {
  id: string;
  provider: string;
  external_ref?: string | null;
  amount_cents: number;
  tip_cents?: number;
  currency?: string;
  status?: string;
}

interface AdjustmentIn {
  id: string;
  kind: "discount" | "surcharge";
  basis: "percent" | "amount";
  value: number;
  label?: string | null;
}

interface RefundIn {
  id: string;
  payment_id: string;
  amount_cents: number;
  reason: string;
  provider: string;
  external_ref?: string | null;
}

const byId = <T>(key: (x: T) => string) => (a: T, b: T) => (key(a) < key(b) ? -1 : key(a) > key(b) ? 1 : 0);

export function lineOut(line: LineIn): OrderLineOut {
  return {
    id: line.id,
    parent_line_id: line.parent_line_id ?? null,
    menu_item_id: line.menu_item_id ?? null,
    modifier_id: line.modifier_id ?? null,
    quantity: line.quantity ?? 1,
    name: line.name_snapshot,
    kitchen_name: line.kitchen_name_snapshot ?? null,
    unit_price_cents: line.unit_price_cents,
    tax_rate: line.tax_rate_snapshot ?? 0,
    category: line.category_snapshot ?? null,
    seat_number: line.seat_number ?? null,
    course_number: line.course_number ?? null,
    sent: false,
    removed: false,
  };
}

/** Recompute everything derived: totals, GST, balance, tips, refunds, unsent lines. */
export function withMoney(order: OrderResponse): OrderResponse {
  const lines = [...order.lines].sort(byId((l) => l.id));
  const active = lines.filter((l) => !l.removed);
  const adjustments = [...order.adjustments].sort(byId((a) => a.id));
  const payments = [...order.payments].sort(byId((p) => p.payment_id));
  const refunds = [...order.refunds].sort(byId((r) => r.refund_id));
  const money = orderMath(active, adjustments);
  const captured = payments.filter((p) => p.status === "captured");
  const paid = captured.reduce((sum, p) => sum + p.amount_cents, 0);
  return {
    ...order,
    lines,
    adjustments: adjustments.map((a) => ({ ...a, amount_cents: money.adjustments[a.id] ?? 0 })),
    payments,
    refunds,
    subtotal_cents: money.subtotal_cents,
    total_cents: money.total_cents,
    gst_cents: money.gst_cents,
    balance_cents: Math.max(money.total_cents - paid, 0),
    tips_cents: captured.reduce((sum, p) => sum + p.tip_cents, 0),
    refunded_cents: refunds.reduce((sum, r) => sum + r.amount_cents, 0),
    unsent_lines: active.filter((l) => !l.sent).length,
  };
}

export function capturedCents(order: OrderResponse): number {
  return order.payments
    .filter((p) => p.status === "captured")
    .reduce((sum, p) => sum + p.amount_cents, 0);
}

export function isUnsettledState(state: string): boolean {
  return UNSETTLED.has(state);
}

export function summaryOf(order: OrderResponse): OrderSummary {
  return {
    id: order.id,
    order_number: order.order_number,
    state: order.state,
    fulfillment: order.fulfillment,
    table_label: order.table_label,
    business_date: order.business_date,
    created_at: order.created_at,
    subtotal_cents: order.subtotal_cents,
    total_cents: order.total_cents,
    gst_cents: order.gst_cents,
    balance_cents: order.balance_cents,
    tips_cents: order.tips_cents,
    refunded_cents: order.refunded_cents,
    unsent_lines: order.unsent_lines,
  };
}

function placed(event: SyncEvent): OrderResponse {
  const p = event.payload as {
    order_id: string;
    order_number: number;
    fulfillment: string;
    table_label?: string | null;
    lines: LineIn[];
  };
  return withMoney({
    id: p.order_id,
    order_number: p.order_number,
    state: "open",
    fulfillment: p.fulfillment,
    table_label: p.table_label ?? null,
    business_date: event.business_date,
    created_at: event.occurred_at,
    subtotal_cents: 0,
    total_cents: 0,
    gst_cents: 0,
    balance_cents: 0,
    tips_cents: 0,
    refunded_cents: 0,
    unsent_lines: 0,
    lines: p.lines.map(lineOut),
    adjustments: [],
    payments: [],
    refunds: [],
    replayed: false,
  });
}

/** After items come off or adjustments change: paid once what's been paid covers it (a $0
 *  order with nothing paid stays open). */
function settled(order: OrderResponse): Applied {
  const next = withMoney(order);
  if (capturedCents(next) > 0 && next.balance_cents === 0) {
    return { order: { ...next, state: "paid" }, status: "applied", detail: "now paid in full" };
  }
  return { order: next, status: "applied" };
}

function paymentOut(p: PaymentIn): PaymentOut {
  return {
    payment_id: p.id,
    provider: p.provider,
    external_ref: p.external_ref ?? null,
    amount_cents: p.amount_cents,
    tip_cents: p.tip_cents ?? 0,
    currency: p.currency ?? "AUD",
    status: p.status ?? "captured",
  };
}

function refundOut(r: RefundIn): RefundOut {
  return {
    refund_id: r.id,
    payment_id: r.payment_id,
    amount_cents: r.amount_cents,
    reason: r.reason,
    provider: r.provider,
    external_ref: r.external_ref ?? null,
  };
}

/** The order after `event`, and what applying it did (as the server would report it). */
export function applyEvent(order: OrderResponse | null, event: SyncEvent): Applied {
  const p = event.payload as Record<string, unknown>;
  if (event.type === "order.placed") {
    return order ? { order, status: "duplicate", detail: "order already here" } : { order: placed(event), status: "applied" };
  }
  if (!order) return { order: null, status: "ignored", detail: "order not found" };
  const unsettled = UNSETTLED.has(order.state);

  switch (event.type) {
    case "order.lines_added": {
      if (!unsettled) return { order, status: "conflict", detail: `can't add to a ${order.state} order` };
      const have = new Set(order.lines.map((l) => l.id));
      const fresh = (p.lines as LineIn[]).filter((l) => !have.has(l.id)).map(lineOut);
      if (fresh.length === 0) return { order, status: "duplicate", detail: "lines already here" };
      return { order: withMoney({ ...order, lines: [...order.lines, ...fresh] }), status: "applied" };
    }
    case "order.sent": {
      if (order.state === "voided") return { order, status: "conflict", detail: "voided -> sent not allowed" };
      const only = Array.isArray(p.line_ids) ? new Set(p.line_ids as string[]) : null;
      let marked = 0;
      const lines = order.lines.map((l) => {
        if (l.sent || l.removed || (only && !only.has(l.id))) return l;
        marked += 1;
        return { ...l, sent: true };
      });
      const next = withMoney({ ...order, lines });
      if (order.state === "open") return { order: { ...next, state: "sent" }, status: "applied" };
      if (marked) return { order: next, status: "applied", detail: `${marked} more lines to the kitchen` };
      return { order, status: "duplicate", detail: `already ${order.state}` };
    }
    case "order.voided": {
      if (order.state === "voided") return { order, status: "duplicate", detail: "already voided" };
      if (!canTransition(order.state, "voided")) {
        return { order, status: "conflict", detail: `${order.state} -> voided not allowed` };
      }
      return { order: { ...order, state: "voided" }, status: "applied" };
    }
    case "order.paid": {
      const pay = p.payment as PaymentIn | null | undefined;
      // Money moved: keep the payment even when the state can't change, as the server does.
      let next = order;
      if (pay && !order.payments.some((x) => x.payment_id === pay.id)) {
        next = withMoney({ ...order, payments: [...order.payments, paymentOut(pay)] });
      }
      if (order.state === "paid") return { order: next, status: "duplicate", detail: "already paid" };
      if (!canTransition(order.state, "paid")) {
        return { order: next, status: "conflict", detail: `${order.state} -> paid not allowed` };
      }
      if (pay && next.balance_cents > 0) {
        return { order: next, status: "applied", detail: `part payment; ${next.balance_cents} cents owing` };
      }
      return { order: { ...next, state: "paid" }, status: "applied" };
    }
    case "order.lines_removed": {
      if (!unsettled) return { order, status: "conflict", detail: `can't change a ${order.state} order` };
      const gone = new Set(p.line_ids as string[]);
      const lines = order.lines.map((l) =>
        !l.removed && (gone.has(l.id) || (l.parent_line_id !== null && gone.has(l.parent_line_id)))
          ? { ...l, removed: true }
          : l,
      );
      return settled({ ...order, lines });
    }
    case "order.adjusted": {
      if (!unsettled) return { order, status: "conflict", detail: `can't change a ${order.state} order` };
      const adj = p.adjustment as AdjustmentIn;
      if (order.adjustments.some((a) => a.id === adj.id)) {
        return { order, status: "duplicate", detail: "adjustment already here" };
      }
      const added: AdjustmentOut = {
        id: adj.id,
        kind: adj.kind,
        basis: adj.basis,
        value: adj.value,
        label: adj.label ?? null,
        amount_cents: 0,
      };
      return settled({ ...order, adjustments: [...order.adjustments, added] });
    }
    case "order.adjustment_removed": {
      if (!unsettled) return { order, status: "conflict", detail: `can't change a ${order.state} order` };
      const id = p.adjustment_id as string;
      if (!order.adjustments.some((a) => a.id === id)) {
        return { order, status: "duplicate", detail: "adjustment already removed" };
      }
      return settled({ ...order, adjustments: order.adjustments.filter((a) => a.id !== id) });
    }
    case "order.refunded": {
      const r = p.refund as RefundIn;
      if (order.refunds.some((x) => x.refund_id === r.id)) {
        return { order, status: "duplicate", detail: "refund already here" };
      }
      const next = withMoney({ ...order, refunds: [...order.refunds, refundOut(r)] });
      const taken = next.payments
        .filter((x) => x.status === "captured")
        .reduce((sum, x) => sum + x.amount_cents + x.tip_cents, 0);
      const state = next.state === "paid" && next.refunded_cents >= taken ? "refunded" : next.state;
      return { order: { ...next, state }, status: "applied" };
    }
    default:
      return { order, status: "ignored", detail: `no handler for ${event.type}` };
  }
}

/** Fold several events onto an order, in order. */
export function applyAll(order: OrderResponse | null, events: SyncEvent[]): OrderResponse | null {
  return events.reduce<OrderResponse | null>((o, e) => applyEvent(o, e).order, order);
}

/** The order an event is about, if any. */
export function orderIdOf(event: SyncEvent): string | null {
  const id = (event.payload as { order_id?: unknown }).order_id;
  return typeof id === "string" ? id : null;
}
