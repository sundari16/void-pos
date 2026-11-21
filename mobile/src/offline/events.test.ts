import { ApiError } from "../lib/api";
import {
  addLinesEvent,
  adjustEvent,
  businessDate,
  dayBefore,
  kitchenEvent,
  occurredNow,
  payEvent,
  placeEvent,
  removeAdjustmentEvent,
  removeLinesEvent,
  seedOccurred,
} from "./events";
import { applyEvent } from "./projection";
import { cmd, dosa, ghee, placeBody, placed, TILL } from "./testkit";

function refusal(fn: () => unknown): ApiError {
  try {
    fn();
  } catch (err) {
    if (err instanceof ApiError) return err;
    throw err;
  }
  throw new Error("expected a refusal");
}

describe("till commands as sync events", () => {
  const { order } = placed(1, "O1", [dosa("L1"), ghee("L2", "L1")]);

  it("records a placement exactly as the online route does", () => {
    const body = placeBody(7, "O1", [dosa("L1")]);
    const event = placeEvent(TILL, body);
    expect(event).toEqual({
      event_id: body.event_id,
      device_id: TILL.deviceId,
      seq: 7,
      location_id: TILL.locationId,
      business_date: "2026-11-21",
      occurred_at: body.occurred_at,
      type: "order.placed",
      payload: {
        order_id: "O1",
        order_number: 1001,
        channel: "pos",
        fulfillment: "dine_in",
        customer_id: null,
        table_label: "4",
        delivery_address: null,
        customer_note: null,
        lines: [
          {
            id: "L1",
            parent_line_id: null,
            menu_item_id: null,
            modifier_id: null,
            quantity: 1,
            name_snapshot: "Masala Dosa",
            kitchen_name_snapshot: null,
            unit_price_cents: 1850,
            tax_rate_snapshot: 0.1,
            category_snapshot: "Mains",
            seat_number: null,
            course_number: null,
          },
        ],
      },
    });
  });

  it("dates a placement in the location's timezone when the till didn't", () => {
    const body = { ...placeBody(2, "O2", [dosa("L1")]), business_date: null, occurred_at: undefined };
    const event = placeEvent(TILL, body);
    expect(event.business_date).toBe(businessDate(TILL.timezone));
    expect(event.occurred_at).toMatch(/Z$/);
  });

  it("never stamps an event before the till's last one, whatever the clock does", () => {
    // Far-future times, so the real clock (other tests stamp events "now") never overtakes them.
    const first = occurredNow(Date.parse("2100-01-01T03:00:00.000Z"));
    const stepBack = occurredNow(Date.parse("2100-01-01T02:59:00.000Z"));
    expect(stepBack > first).toBe(true);
    seedOccurred("2100-01-01T04:00:00.000Z");
    expect(occurredNow(Date.parse("2100-01-01T03:30:00.000Z"))).toBe("2100-01-01T04:00:00.001Z");
    seedOccurred("not a date");
    expect(occurredNow(Date.parse("2100-01-01T05:00:00.000Z"))).toBe("2100-01-01T05:00:00.000Z");
  });

  it("works out business dates", () => {
    // 9pm UTC on the 20th is 7am on the 21st in Brisbane.
    expect(businessDate("Australia/Brisbane", new Date("2026-11-20T21:00:00Z"))).toBe("2026-11-21");
    expect(businessDate("UTC", new Date("2026-11-20T21:00:00Z"))).toBe("2026-11-20");
    expect(dayBefore("2026-03-01")).toBe("2026-02-28");
    expect(dayBefore("2027-01-01")).toBe("2026-12-31");
  });

  it("dates changes to an order by the order's own business date", () => {
    const old = { ...order, business_date: "2026-11-20" };
    expect(addLinesEvent(TILL, old, { ...cmd(2), lines: [dosa("L3")] }).business_date).toBe("2026-11-20");
  });

  it("pays the balance by default, and refuses more than is owing", () => {
    const event = payEvent(TILL, order, { ...cmd(2), payment_id: "P1", method: "external", external_ref: "AUTH 42" });
    expect(event.payload.payment).toEqual({
      id: "P1",
      provider: "external",
      external_ref: "AUTH 42",
      amount_cents: 2000,
      tip_cents: 0,
      currency: "AUD",
      status: "captured",
    });
    const cash = payEvent(TILL, order, { ...cmd(3), payment_id: "P2", method: "cash", external_ref: "ignored", amount_cents: 500 });
    expect(cash.payload.payment).toMatchObject({ provider: "cash", external_ref: null, amount_cents: 500 });

    expect(refusal(() => payEvent(TILL, order, { ...cmd(4), payment_id: "P3", method: "cash", amount_cents: 2001 })).code).toBe(
      "amount_exceeds_balance",
    );
    expect(refusal(() => payEvent(TILL, order, { ...cmd(4), payment_id: "P3", method: "cash", amount_cents: 0 })).code).toBe(
      "invalid_amount",
    );
  });

  it("leaves Stripe, discounts and taking off sent items to the server", () => {
    expect(refusal(() => payEvent(TILL, order, { ...cmd(2), payment_id: "P1", method: "stripe" })).code).toBe("needs_server");
    const discount = { ...cmd(2), id: "A1", kind: "discount" as const, basis: "percent" as const, value: 1000 };
    expect(refusal(() => adjustEvent(TILL, order, discount)).code).toBe("needs_server");
    const sent = applyEvent(order, kitchenEvent(TILL, order, cmd(3))).order!;
    expect(refusal(() => removeLinesEvent(TILL, sent, { ...cmd(4), line_ids: ["L1"], reason: "x" })).code).toBe(
      "needs_server",
    );
  });

  it("refuses what the server would refuse", () => {
    const voided = { ...order, state: "voided" };
    expect(refusal(() => kitchenEvent(TILL, voided, cmd(2))).code).toBe("order_voided");
    expect(refusal(() => addLinesEvent(TILL, voided, { ...cmd(2), lines: [dosa("L3")] })).code).toBe("order_closed");
    expect(refusal(() => removeLinesEvent(TILL, order, { ...cmd(2), line_ids: ["L2"], reason: "x" })).code).toBe(
      "unknown_line",
    );
    expect(refusal(() => removeAdjustmentEvent(TILL, order, "A9", cmd(2))).code).toBe("unknown_adjustment");

    const paid = applyEvent(order, payEvent(TILL, order, { ...cmd(3), payment_id: "P1", method: "cash", amount_cents: 1500 })).order!;
    expect(refusal(() => removeLinesEvent(TILL, paid, { ...cmd(4), line_ids: ["L1"], reason: "x" })).code).toBe("below_paid");
  });
});
