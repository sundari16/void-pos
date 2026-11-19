import {
  isUnsettled,
  placedItemCents,
  placedItems,
  refundable,
  sendLabel,
  withKitchen,
  withPayment,
} from "./placed";
import type { KitchenResponse, OrderLineOut, OrderResponse, PaymentResponse } from "./types";

function line(partial: Partial<OrderLineOut> = {}): OrderLineOut {
  return {
    id: "l1",
    parent_line_id: null,
    menu_item_id: "m",
    modifier_id: null,
    quantity: 1,
    name: "Naan",
    kitchen_name: null,
    unit_price_cents: 350,
    tax_rate: 0,
    category: null,
    seat_number: null,
    course_number: null,
    sent: false,
    removed: false,
    ...partial,
  };
}

function order(partial: Partial<OrderResponse> = {}): OrderResponse {
  return {
    id: "o1",
    order_number: 100,
    state: "open",
    fulfillment: "dine_in",
    table_label: null,
    business_date: "2026-11-20",
    created_at: "2026-11-20T00:00:00Z",
    subtotal_cents: 0,
    total_cents: 0,
    gst_cents: 0,
    balance_cents: 0,
    tips_cents: 0,
    refunded_cents: 0,
    unsent_lines: 0,
    lines: [],
    adjustments: [],
    payments: [],
    refunds: [],
    replayed: false,
    ...partial,
  };
}

describe("placedItems", () => {
  it("groups each item with its modifier child lines, dropping removed lines", () => {
    const items = placedItems([
      line({ id: "item", parent_line_id: null, quantity: 2 }),
      line({ id: "mod", parent_line_id: "item", modifier_id: "butter", unit_price_cents: 150 }),
      line({ id: "gone", parent_line_id: null, removed: true }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0].line.id).toBe("item");
    expect(items[0].modifiers).toHaveLength(1);
    // (350 + 150) * 2
    expect(placedItemCents(items[0])).toBe(1000);
  });
});

describe("isUnsettled", () => {
  it("is true while open or sent", () => {
    expect(isUnsettled(order({ state: "open" }))).toBe(true);
    expect(isUnsettled(order({ state: "sent" }))).toBe(true);
    expect(isUnsettled(order({ state: "paid" }))).toBe(false);
  });
});

describe("withKitchen / withPayment", () => {
  it("marks every line sent after a kitchen send", () => {
    const sent = withKitchen(order({ lines: [line({ sent: false })], unsent_lines: 1 }), {
      order_id: "o1",
      state: "sent",
      printed: true,
      ticket_bytes: 10,
      lines_sent: 1,
      replayed: false,
    } satisfies KitchenResponse);
    expect(sent.state).toBe("sent");
    expect(sent.unsent_lines).toBe(0);
    expect(sent.lines[0].sent).toBe(true);
  });

  it("applies a payment to the balance and state", () => {
    const paid = withPayment(order({ total_cents: 1000, balance_cents: 1000 }), {
      order_id: "o1",
      payment_id: "p1",
      provider: "cash",
      amount_cents: 1000,
      tip_cents: 0,
      currency: "AUD",
      status: "captured",
      order_state: "paid",
      balance_cents: 0,
      replayed: false,
    } satisfies PaymentResponse);
    expect(paid.state).toBe("paid");
    expect(paid.balance_cents).toBe(0);
    expect(paid.payments).toHaveLength(1);
  });
});

describe("sendLabel", () => {
  it("reflects what the next send will print", () => {
    expect(sendLabel(order({ unsent_lines: 0 }))).toBe("Reprint kitchen ticket");
    expect(sendLabel(order({ unsent_lines: 1, lines: [line({ sent: false })] }))).toBe(
      "Send to kitchen",
    );
    expect(
      sendLabel(
        order({
          unsent_lines: 1,
          lines: [line({ id: "a", sent: true }), line({ id: "b", sent: false })],
        }),
      ),
    ).toBe("Send 1 new item to kitchen");
  });
});

describe("refundable", () => {
  it("reports how much of a captured payment can still be given back", () => {
    const o = order({
      payments: [
        {
          payment_id: "p1",
          provider: "cash",
          external_ref: null,
          amount_cents: 1000,
          tip_cents: 0,
          currency: "AUD",
          status: "captured",
        },
      ],
    });
    expect(refundable(o)).toEqual([{ payment: o.payments[0], left: 1000 }]);
  });
});
