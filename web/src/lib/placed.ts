// A placed order on the till: its lines grouped for display, and server answers applied
// to it. Pure functions, so the till's view of an order can't drift from the server's.

import type {
  KitchenResponse,
  OrderLineOut,
  OrderResponse,
  PaymentResponse,
  VoidResponse,
} from "./types";

export interface PlacedItem {
  line: OrderLineOut;
  modifiers: OrderLineOut[];
}

/** Item lines in order, each with its modifier lines. */
export function placedItems(lines: OrderLineOut[]): PlacedItem[] {
  const items: PlacedItem[] = [];
  const byId = new Map<string, PlacedItem>();
  for (const line of lines) {
    if (line.parent_line_id === null) {
      const item = { line, modifiers: [] };
      items.push(item);
      byId.set(line.id, item);
    }
  }
  for (const line of lines) {
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

/** The kitchen has every line now. */
export function withKitchen(order: OrderResponse, kitchen: KitchenResponse): OrderResponse {
  return {
    ...order,
    state: kitchen.state,
    lines: order.lines.map((line) => ({ ...line, sent: true })),
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
  if (order.lines.every((line) => !line.sent)) return "Send to kitchen";
  const items = order.lines.filter((l) => !l.sent && l.parent_line_id === null).length;
  return `Send ${items} new item${items === 1 ? "" : "s"} to kitchen`;
}
