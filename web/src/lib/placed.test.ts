import { describe, expect, it } from "vitest";
import {
  isUnsettled,
  placedItemCents,
  placedItems,
  sendLabel,
  withKitchen,
  withPayment,
  withVoid,
} from "./placed";
import type { OrderLineOut, OrderResponse } from "./types";

function line(id: string, price: number, qty = 1, parent: string | null = null, sent = false): OrderLineOut {
  return {
    id,
    parent_line_id: parent,
    menu_item_id: null,
    modifier_id: null,
    quantity: qty,
    name: id,
    kitchen_name: null,
    unit_price_cents: price,
    tax_rate: 0.1,
    category: null,
    seat_number: null,
    course_number: null,
    sent,
  };
}

const order: OrderResponse = {
  id: "o1",
  order_number: 1000,
  state: "sent",
  fulfillment: "dine_in",
  table_label: "12",
  business_date: "2026-11-18",
  created_at: "2026-11-18T09:00:00Z",
  total_cents: 4800,
  gst_cents: 436,
  balance_cents: 4800,
  unsent_lines: 2,
  lines: [
    line("dosa", 1850, 2, null, true),
    line("hot", 0, 1, "dosa", true),
    line("naan", 450, 2),
    line("butter", 100, 1, "naan"),
  ],
  payments: [],
  replayed: false,
};

describe("placed orders", () => {
  it("group modifier lines under their items and price them per unit", () => {
    const items = placedItems(order.lines);
    expect(items.map((i) => [i.line.id, i.modifiers.map((m) => m.id)])).toEqual([
      ["dosa", ["hot"]],
      ["naan", ["butter"]],
    ]);
    expect(items.map(placedItemCents)).toEqual([3700, 1100]);
  });

  it("say what sending will do", () => {
    expect(sendLabel(order)).toBe("Send 1 new item to kitchen");
    expect(sendLabel({ ...order, lines: order.lines.map((l) => ({ ...l, sent: false })) })).toBe(
      "Send to kitchen",
    );
    const sent = withKitchen(order, {
      order_id: "o1",
      state: "sent",
      printed: true,
      ticket_bytes: 10,
      lines_sent: 2,
      replayed: false,
    });
    expect(sent.lines.every((l) => l.sent)).toBe(true);
    expect(sendLabel(sent)).toBe("Reprint kitchen ticket");
  });

  it("take payments once, and voids", () => {
    const payment = {
      order_id: "o1",
      payment_id: "p1",
      provider: "cash",
      amount_cents: 800,
      currency: "AUD",
      status: "captured",
      order_state: "sent",
      balance_cents: 4000,
      replayed: false,
    };
    const paid = withPayment(withPayment(order, payment), { ...payment, replayed: true });
    expect(paid.payments).toHaveLength(1);
    expect(paid.balance_cents).toBe(4000);
    expect(isUnsettled(paid)).toBe(true);
    const voided = withVoid(order, { order_id: "o1", state: "voided", printed: false, replayed: false });
    expect(isUnsettled(voided)).toBe(false);
  });
});
