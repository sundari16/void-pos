// The offline path against a real Void POS API: two tills ring up and pay one order while
// "offline" (changes queued on the device), then sync. Skipped unless pointed at a server:
//
//   VOIDPOS_E2E_API=http://127.0.0.1:8765 VOIDPOS_E2E_CODES=<code till 1>,<code till 2> npx jest e2e
//
// (see mobile/README.md: seed a SQLite database and run uvicorn on a free port).

import { request as httpRequest } from "node:http";
import { createApi } from "../lib/api";
import type { LineIn, OrderResponse, TillMenuItem } from "../lib/types";
import { ulid } from "../lib/ulid";
import { addLinesEvent, adjustEvent, businessDate, kitchenEvent, payEvent, placeEvent } from "./events";
import { MemoryStore } from "./store";
import { TillSync } from "./sync";

const BASE = process.env.VOIDPOS_E2E_API ?? "";
const CODES = (process.env.VOIDPOS_E2E_CODES ?? "").split(",").filter(Boolean);
const live = BASE && CODES.length >= 2 ? describe : describe.skip;

/** Node's own HTTP: under jest-expo, `fetch` is Expo's native one, stubbed out. */
function nodeFetch(input: string, init?: RequestInit): Promise<Response> {
  return new Promise((resolve, reject) => {
    const req = httpRequest(
      input,
      { method: init?.method ?? "GET", headers: init?.headers as Record<string, string> },
      (res) => {
        let text = "";
        res.setEncoding("utf8");
        res.on("data", (chunk: string) => (text += chunk));
        res.on("end", () => {
          const status = res.statusCode ?? 0;
          resolve({
            ok: status >= 200 && status < 300,
            status,
            statusText: res.statusMessage ?? "",
            json: async () => JSON.parse(text) as unknown,
          } as Response);
        });
      },
    );
    req.on("error", reject);
    if (typeof init?.body === "string") req.write(init.body);
    req.end();
  });
}

const api = createApi(nodeFetch, () => BASE);

async function pairTill(code: string) {
  const pairing = await api.pair(code);
  let seq = pairing.device.last_seq + 1;
  let number = pairing.next_order_number;
  const till = {
    token: pairing.token,
    deviceId: pairing.device.id,
    locationId: pairing.location.id,
    timezone: pairing.location.timezone,
  };
  const store = new MemoryStore();
  const sync = new TillSync({
    store,
    api,
    till: () => till,
    seqAfter: (last) => {
      seq = Math.max(seq, last + 1);
      return seq++;
    },
    orderNumberAfter: (next) => {
      number = Math.max(number, next);
      return number++;
    },
    onUnauthorized: () => {
      throw new Error("the till was refused");
    },
    today: () => businessDate(till.timezone),
  });
  return { till, store, sync, cmd: () => ({ event_id: ulid(), seq: seq++ }), nextNumber: () => number++ };
}

/** What both sides must agree on. */
function money(o: OrderResponse | null) {
  if (!o) return null;
  return {
    state: o.state,
    order_number: o.order_number,
    subtotal_cents: o.subtotal_cents,
    total_cents: o.total_cents,
    gst_cents: o.gst_cents,
    balance_cents: o.balance_cents,
    tips_cents: o.tips_cents,
    unsent_lines: o.unsent_lines,
    lines: o.lines.map((l) => [l.id, l.quantity, l.unit_price_cents, l.sent, l.removed]),
    adjustments: o.adjustments.map((a) => [a.id, a.amount_cents]),
    payments: o.payments.map((p) => [p.payment_id, p.provider, p.amount_cents, p.tip_cents]),
  };
}

live("the offline path against a live API", () => {
  jest.setTimeout(60_000);

  it("queues on two tills, syncs, and agrees with the server to the cent", async () => {
    const a = await pairTill(CODES[0]);
    const b = await pairTill(CODES[1]);
    const menu = await api.tillMenu(a.till.token, a.till.locationId);
    const item = (name: string) => menu.items.find((i) => i.name === name) as TillMenuItem;
    const line = (it: TillMenuItem, quantity = 1): LineIn => ({
      id: ulid(),
      menu_item_id: it.id,
      quantity,
      name_snapshot: it.name,
      unit_price_cents: it.base_price_cents,
      tax_rate_snapshot: it.tax_rate,
      category_snapshot: it.category,
    });
    await b.sync.pull(); // B catches up with the venue first

    // Till A, offline: place, send to the kitchen, add an item, a surcharge, part-pay in cash.
    const orderId = ulid();
    await a.sync.record("push", null, null, () =>
      placeEvent(a.till, {
        ...a.cmd(),
        order_id: orderId,
        order_number: a.nextNumber(),
        channel: "pos",
        fulfillment: "dine_in",
        table_label: "7",
        lines: [line(item("Masala Dosa"), 2)],
      }),
    );
    await a.sync.record("kitchen", orderId, null, (o) => kitchenEvent(a.till, o!, a.cmd()));
    await a.sync.record("push", orderId, null, (o) =>
      addLinesEvent(a.till, o!, { ...a.cmd(), lines: [line(item("Garlic Naan"))] }),
    );
    await a.sync.record("push", orderId, null, (o) =>
      adjustEvent(a.till, o!, { ...a.cmd(), id: ulid(), kind: "surcharge", basis: "percent", value: 1500, label: "Public holiday" }),
    );
    const local = await a.sync.record("push", orderId, null, (o) =>
      payEvent(a.till, o!, { ...a.cmd(), payment_id: ulid(), method: "cash", amount_cents: 1000, tip_cents: 150 }),
    );
    const queued = (await a.sync.pending()).map((e) => e.event);

    // Back online: everything lands, the kitchen send through its route.
    const report = await a.sync.drain();
    expect(report).toMatchObject({ reached: true, done: 5, failed: 0 });
    const server = await api.getOrder(a.till.token, orderId);
    expect(money(local)).toEqual(money(server));
    expect(money(await a.store.order(orderId))).toEqual(money(server));

    // Pushing the same events again changes nothing.
    const again = await api.syncPush(a.till.token, queued.filter((e) => e.type !== "order.sent"));
    expect(new Set(again.acks.map((x) => x.status))).toEqual(new Set(["duplicate"]));

    // Till B pulls A's order and sees what the server sees.
    await b.sync.pull();
    expect(money(await b.store.order(orderId))).toEqual(money(server));

    // B, offline, takes the rest on the card terminal.
    await b.sync.record("push", orderId, null, (o) =>
      payEvent(b.till, o!, { ...b.cmd(), payment_id: ulid(), method: "external", external_ref: "AUTH 77" }),
    );
    expect((await b.sync.drain()).failed).toBe(0);
    expect((await api.getOrder(b.till.token, orderId)).state).toBe("paid");

    // A hasn't heard, and takes another cash payment offline: the server keeps the money
    // and the till says so.
    await a.sync.record("push", orderId, null, (o) =>
      payEvent(a.till, o!, { ...a.cmd(), payment_id: ulid(), method: "cash", amount_cents: 500 }),
    );
    const late = await a.sync.drain();
    expect(late.notices.some((n) => /already paid on another till/.test(n.text))).toBe(true);
    const settled = await api.getOrder(a.till.token, orderId);
    expect([settled.state, settled.payments.length]).toEqual(["paid", 3]);

    // A placement the server refuses holds back the changes that depend on it.
    const bad = ulid();
    await a.sync.record("push", null, null, () =>
      placeEvent(a.till, {
        ...a.cmd(),
        order_id: bad,
        order_number: 999_999,
        channel: "pos",
        fulfillment: "pickup",
        lines: [line(item("Papadum"))],
      }),
    );
    await a.sync.record("push", bad, null, (o) => payEvent(a.till, o!, { ...a.cmd(), payment_id: ulid(), method: "cash" }));
    const refused = await a.sync.drain();
    expect([refused.done, refused.failed]).toEqual([0, 2]);
    expect((await a.store.entries()).map((e) => e.code)).toEqual(["order_number_out_of_range", "order_not_synced"]);
  });
});
