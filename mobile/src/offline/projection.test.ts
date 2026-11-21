import type { OrderResponse, SyncEvent } from "../lib/types";
import {
  addLinesEvent,
  adjustEvent,
  kitchenEvent,
  payEvent,
  removeAdjustmentEvent,
  removeLinesEvent,
} from "./events";
import { applyAll, applyEvent, summaryOf } from "./projection";
import { cmd, dosa, ghee, placed, TILL } from "./testkit";

function foreign(type: string, payload: Record<string, unknown>, seq = 1): SyncEvent {
  return {
    event_id: `X${String(seq).padStart(25, "0")}`,
    device_id: "01JDEV00000000000000000002",
    seq,
    location_id: TILL.locationId,
    business_date: "2026-11-21",
    occurred_at: "2026-11-21T05:00:00.000Z",
    type,
    payload,
  };
}

const pay = (order: OrderResponse, seq: number, amount: number, id = `P${seq}`, tip = 0) =>
  payEvent(TILL, order, { ...cmd(seq), payment_id: id, method: "cash", amount_cents: amount, tip_cents: tip });

describe("projecting events onto an order", () => {
  const start = placed(1, "O1", [dosa("L1", 2), ghee("L2", "L1")]);

  it("places an order with the server's totals", () => {
    const o = start.order;
    expect([o.state, o.order_number, o.table_label, o.business_date]).toEqual(["open", 1001, "4", "2026-11-21"]);
    expect([o.subtotal_cents, o.total_cents, o.gst_cents, o.balance_cents]).toEqual([4000, 4000, 364, 4000]);
    expect(o.unsent_lines).toBe(2);
    expect(o.lines.map((l) => [l.id, l.name, l.sent, l.removed])).toEqual([
      ["L1", "Masala Dosa", false, false],
      ["L2", "Ghee roast", false, false],
    ]);
    expect(summaryOf(o)).not.toHaveProperty("lines");
  });

  it("sends to the kitchen, then just the items added since", () => {
    const sent = applyEvent(start.order, kitchenEvent(TILL, start.order, cmd(2)));
    expect([sent.status, sent.order?.state, sent.order?.unsent_lines]).toEqual(["applied", "sent", 0]);

    const added = applyEvent(sent.order, addLinesEvent(TILL, sent.order!, { ...cmd(3), lines: [dosa("L3")] })).order!;
    expect([added.total_cents, added.unsent_lines]).toEqual([5850, 1]);
    const again = kitchenEvent(TILL, added, cmd(4));
    expect(again.payload.line_ids).toEqual(["L3"]);
    expect(applyEvent(added, again).order?.unsent_lines).toBe(0);
    expect(applyEvent(applyEvent(added, again).order, again).status).toBe("duplicate");
  });

  it("is paid once payments cover the total, and a payment counts once", () => {
    const part = applyEvent(start.order, pay(start.order, 2, 1500));
    expect([part.status, part.order?.state, part.order?.balance_cents]).toEqual(["applied", "open", 2500]);
    expect(part.detail).toMatch(/2500 cents owing/);
    const whole = applyEvent(part.order, pay(part.order!, 3, 2500, "P3", 300)).order!;
    expect([whole.state, whole.balance_cents, whole.tips_cents]).toEqual(["paid", 0, 300]);
    expect(applyEvent(whole, pay(part.order!, 3, 2500, "P3", 300)).order?.payments).toHaveLength(2);
  });

  it("keeps a payment on an order voided elsewhere, as a conflict to reconcile", () => {
    const voided = applyEvent(start.order, foreign("order.voided", { order_id: "O1", reason: "x" })).order!;
    const late = applyEvent(voided, pay(start.order, 3, 4000));
    expect([late.status, late.order?.state, late.order?.payments.length]).toEqual(["conflict", "voided", 1]);
  });

  it("takes items off with their modifiers, and settles a part-paid order", () => {
    const withMore = applyEvent(start.order, addLinesEvent(TILL, start.order, { ...cmd(2), lines: [dosa("L3")] })).order!;
    const paid = applyEvent(withMore, pay(withMore, 3, 4000)).order!;
    expect([paid.state, paid.balance_cents]).toEqual(["open", 1850]);
    const off = applyEvent(paid, removeLinesEvent(TILL, paid, { ...cmd(4), line_ids: ["L3"], reason: "Out of stock" }));
    expect([off.status, off.detail, off.order?.state, off.order?.balance_cents]).toEqual([
      "applied",
      "now paid in full",
      "paid",
      0,
    ]);

    const both = applyEvent(start.order, removeLinesEvent(TILL, start.order, { ...cmd(5), line_ids: ["L1"], reason: "x" }));
    expect(both.order?.lines.every((l) => l.removed)).toBe(true);
    expect([both.order?.total_cents, both.order?.unsent_lines]).toEqual([0, 0]);
  });

  it("adds and takes off a surcharge, with its amount", () => {
    const body = { ...cmd(2), id: "A1", kind: "surcharge" as const, basis: "percent" as const, value: 1500, label: "Public holiday" };
    const on = applyEvent(start.order, adjustEvent(TILL, start.order, body)).order!;
    expect(on.adjustments.map((a) => [a.id, a.label, a.amount_cents])).toEqual([["A1", "Public holiday", 600]]);
    expect([on.total_cents, on.balance_cents]).toEqual([4600, 4600]);
    const off = applyEvent(on, removeAdjustmentEvent(TILL, on, "A1", cmd(3))).order!;
    expect([off.adjustments.length, off.total_cents]).toEqual([0, 4000]);
  });

  it("folds another till's discount and refund like the server", () => {
    const discounted = applyEvent(
      start.order,
      foreign("order.adjusted", {
        order_id: "O1",
        adjustment: { id: "A1", kind: "discount", basis: "percent", value: 1000, label: null },
      }),
    ).order!;
    expect([discounted.total_cents, discounted.gst_cents]).toEqual([3600, 327]);
    const paid = applyEvent(discounted, pay(discounted, 2, 3600)).order!;
    const refund = (id: string, cents: number) =>
      foreign("order.refunded", {
        order_id: "O1",
        refund: { id, payment_id: "P2", amount_cents: cents, reason: "Cold", provider: "cash" },
      });
    const part = applyEvent(paid, refund("R1", 1000)).order!;
    expect([part.state, part.refunded_cents]).toEqual(["paid", 1000]);
    const all = applyEvent(part, refund("R2", 2600)).order!;
    expect([all.state, all.refunded_cents]).toEqual(["refunded", 3600]);
  });

  it("ignores changes to an order it doesn't have, and refuses changes to a closed one", () => {
    expect(applyEvent(null, foreign("order.paid", { order_id: "O9" })).status).toBe("ignored");
    const voided = applyEvent(start.order, foreign("order.voided", { order_id: "O1" })).order!;
    expect(applyEvent(voided, foreign("order.lines_added", { order_id: "O1", lines: [dosa("L9")] })).status).toBe(
      "conflict",
    );
    expect(applyEvent(voided, foreign("order.sent", { order_id: "O1" })).status).toBe("conflict");
    expect(applyEvent(start.order, start.event).status).toBe("duplicate");
  });

  it("gives the same order however often the events are folded on", () => {
    const events = [
      kitchenEvent(TILL, start.order, cmd(2)),
      pay(start.order, 3, 1000),
      foreign("order.lines_added", { order_id: "O1", lines: [dosa("L5")] }),
    ];
    const once = applyAll(start.order, events);
    expect(applyAll(once, events)).toEqual(once);
    expect(applyAll(applyAll(once, events.slice(0, 1)), events)).toEqual(once);
  });
});
