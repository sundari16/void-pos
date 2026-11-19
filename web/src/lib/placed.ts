// A placed order on the till: its lines grouped for display, and server answers applied
// to it. Pure functions, so the till's view of an order can't drift from the server's.

import type {
  AdjustmentOut,
  KitchenResponse,
  OrderLineOut,
  OrderResponse,
  PaymentOut,
  PaymentResponse,
  VoidResponse,
} from "./types";

export interface PlacedItem {
  line: OrderLineOut;
  modifiers: OrderLineOut[];
}

/** Item lines in order, each with its modifier lines (lines taken off left out). */
export function placedItems(lines: OrderLineOut[]): PlacedItem[] {
  const items: PlacedItem[] = [];
  const byId = new Map<string, PlacedItem>();
  for (const line of lines) {
    if (line.removed) continue;
    if (line.parent_line_id === null) {
      const item = { line, modifiers: [] };
      items.push(item);
      byId.set(line.id, item);
    }
  }
  for (const line of lines) {
    if (line.removed) continue;
    if (line.parent_line_id !== null) byId.get(line.parent_line_id)?.modifiers.push(line);
  }
  return items;
}

/** An item line's amount with its modifiers (modifiers are per unit of the item). */
export function placedItemCents(item: PlacedItem): number {
  const each = item.modifiers.reduce((sum, m) => sum + m.unit_price_cents * m.quantity, 0);
  return (item.line.unit_price_cents + each) * item.line.quantity;
}

export function isUnsettled(order: OrderResponse): boolean {
  return order.state === "open" || order.state === "sent";
}

/** The kitchen has every line now (a fresh send covers every line not yet sent). */
export function withKitchen(order: OrderResponse, kitchen: KitchenResponse): OrderResponse {
  return {
    ...order,
    state: kitchen.state,
    lines: order.lines.map((line) => (line.removed ? line : { ...line, sent: true })),
    unsent_lines: 0,
  };
}

export function withPayment(order: OrderResponse, payment: PaymentResponse): OrderResponse {
  const seen = order.payments.some((p) => p.payment_id === payment.payment_id);
  return {
    ...order,
    state: payment.order_state,
    balance_cents: payment.balance_cents,
    payments: seen
      ? order.payments
      : [
          ...order.payments,
          {
            payment_id: payment.payment_id,
            provider: payment.provider,
            external_ref: null,
            amount_cents: payment.amount_cents,
            tip_cents: payment.tip_cents,
            currency: payment.currency,
            status: payment.status,
          },
        ],
  };
}

export function withVoid(order: OrderResponse, voided: VoidResponse): OrderResponse {
  return { ...order, state: voided.state };
}

/** What the Send button should say for this order. */
export function sendLabel(order: OrderResponse): string {
  if (order.unsent_lines === 0) return "Reprint kitchen ticket";
  if (order.lines.filter((l) => !l.removed).every((line) => !line.sent)) return "Send to kitchen";
  const items = order.lines.filter((l) => !l.sent && !l.removed && l.parent_line_id === null).length;
  return `Send ${items} new item${items === 1 ? "" : "s"} to kitchen`;
}

/** How much of each captured payment can still be given back (tip included). */
export function refundable(order: OrderResponse): { payment: PaymentOut; left: number }[] {
  return order.payments
    .filter((p) => p.status === "captured")
    .map((payment) => {
      const given = order.refunds
        .filter((r) => r.payment_id === payment.payment_id)
        .reduce((sum, r) => sum + r.amount_cents, 0);
      return { payment, left: payment.amount_cents + payment.tip_cents - given };
    })
    .filter((x) => x.left > 0);
}

/** "10% discount", "Holiday surcharge 15%", "$5.00 off": what an adjustment is. */
export function adjustmentLabel(adj: AdjustmentOut): string {
  if (adj.label) return adj.label;
  const what = adj.kind === "discount" ? "Discount" : "Surcharge";
  if (adj.basis === "percent") return `${what} ${percentText(adj.value)}`;
  return what;
}

/** Basis points as a percentage for people: 1000 -> "10%", 1250 -> "12.5%". */
export function percentText(basisPoints: number): string {
  return `${(basisPoints / 100).toFixed(2).replace(/\.?0+$/, "")}%`;
}

/** A tip as a share of what's being paid, in whole cents. */
export function tipFor(amountCents: number, basisPoints: number): number {
  return Math.round((amountCents * basisPoints) / 10_000);
}
