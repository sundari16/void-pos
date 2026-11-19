import { describe, expect, it } from "vitest";
import {
  adjustmentLabel,
  isUnsettled,
  percentText,
  refundable,
  tipFor,
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
    removed: false,
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
  subtotal_cents: 4800,
  total_cents: 4800,
  gst_cents: 436,
  balance_cents: 4800,
  unsent_lines: 2,
  tips_cents: 0,
  refunded_cents: 0,
  adjustments: [],
  refunds: [],
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
      tip_cents: 0,
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

describe("adjustments, tips and refunds", () => {
  it("leave lines taken off out of the ticket", () => {
    const lines = order.lines.map((l) => (l.id === "naan" ? { ...l, removed: true } : l));
    expect(placedItems(lines).map((i) => i.line.id)).toEqual(["dosa"]);
  });

  it("name adjustments and tips for people", () => {
    expect(percentText(1000)).toBe("10%");
    expect(percentText(1250)).toBe("12.5%");
    const adj = { id: "a", kind: "surcharge", basis: "percent", value: 1500, label: null, amount_cents: 720 } as const;
    expect(adjustmentLabel(adj)).toBe("Surcharge 15%");
    expect(adjustmentLabel({ ...adj, label: "Public holiday" })).toBe("Public holiday");
    expect(tipFor(4800, 1000)).toBe(480);
    expect(tipFor(4805, 1500)).toBe(721);
  });

  it("know what is left to refund on each payment", () => {
    const paid = {
      ...order,
      payments: [
        { payment_id: "p1", provider: "cash", external_ref: null, amount_cents: 3000, tip_cents: 200, currency: "AUD", status: "captured" },
        { payment_id: "p2", provider: "external", external_ref: "A1", amount_cents: 1800, tip_cents: 0, currency: "AUD", status: "captured" },
      ],
      refunds: [
        { refund_id: "r1", payment_id: "p1", amount_cents: 500, reason: "x", provider: "cash", external_ref: null },
        { refund_id: "r2", payment_id: "p2", amount_cents: 1800, reason: "x", provider: "external", external_ref: "R" },
      ],
    };
    expect(refundable(paid).map((x) => [x.payment.payment_id, x.left])).toEqual([["p1", 2700]]);
  });
});
