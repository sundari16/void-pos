import { ApiError } from "../lib/api";
import type { CommandIn, DeviceSession, OrderResponse, PullResponse, SyncAck, SyncEvent } from "../lib/types";
import { addLinesEvent, kitchenEvent, payEvent, placeEvent } from "./events";
import { applyAll, applyEvent } from "./projection";
import { MemoryStore, newEntry } from "./store";
import { CURSOR_KEY, queuedCounters, TillSync, type SyncApi } from "./sync";
import { cmd, dosa, placeBody, placed, TILL } from "./testkit";

const NET = new ApiError(0, "network_error", "can't reach the server");

/** A stand-in server: acks every push as applied unless told otherwise, and keeps what it
 *  was sent so getOrder can answer with the order as the server would have it. */
function fakeServer() {
  const log: SyncEvent[] = [];
  const calls: string[] = [];
  const server = {
    log,
    calls,
    pushAck: (event: SyncEvent): Partial<SyncAck> | Error | null => log.some((e) => e.event_id === event.event_id) ? { status: "duplicate", detail: "event already in the log" } : null,
    kitchen: (orderId: string, body: CommandIn): unknown => {
      void orderId;
      void body;
      return null;
    },
    pull: [] as PullResponse[],
    lastSeq: 0,
    nextOrderNumber: 1001,
    down: false,
    api: {} as SyncApi,
  };
  server.api = {
    async syncPush(_token, events) {
      calls.push(`push ${events.map((e) => e.seq).join(",")}`);
      if (server.down) throw NET;
      const acks: SyncAck[] = [];
      for (const event of events) {
        const custom = server.pushAck(event);
        if (custom instanceof Error) throw custom;
        const ack = { event_id: event.event_id, status: "applied", detail: null, code: null, ...custom } as SyncAck;
        if (ack.status !== "rejected" && ack.status !== "duplicate") log.push(event);
        acks.push(ack);
      }
      return { acks };
    },
    async syncPull(_token, cursor) {
      calls.push(`pull ${cursor}`);
      if (server.down) throw NET;
      return server.pull.shift() ?? { events: [], next_cursor: cursor, has_more: false };
    },
    async sendToKitchen(_token, orderId, body) {
      calls.push(`kitchen ${body.seq}`);
      if (server.down) throw NET;
      const custom = server.kitchen(orderId, body);
      if (custom instanceof Error) throw custom;
      const order = applyAll(null, log.filter((e) => e.payload.order_id === orderId))!;
      log.push(kitchenEvent(TILL, order, body));
      return { order_id: orderId, state: "sent", printed: true, ticket_bytes: 10, lines_sent: 1, replayed: false };
    },
    async getOrder(_token, orderId) {
      calls.push(`get ${orderId}`);
      if (server.down) throw NET;
      return applyAll(null, log.filter((e) => e.payload.order_id === orderId)) as OrderResponse;
    },
    async me() {
      calls.push("me");
      return { device: { last_seq: server.lastSeq }, next_order_number: server.nextOrderNumber } as DeviceSession;
    },
  };
  return server;
}

function setup() {
  const server = fakeServer();
  const store = new MemoryStore();
  let nextSeq = 100;
  let nextNumber = 2000;
  const unauthorized = jest.fn();
  const sync = new TillSync({
    store,
    api: server.api,
    till: () => TILL,
    seqAfter: (last) => {
      nextSeq = Math.max(nextSeq, last + 1);
      return nextSeq++;
    },
    orderNumberAfter: (next) => {
      nextNumber = Math.max(nextNumber, next);
      return nextNumber++;
    },
    onUnauthorized: unauthorized,
    today: () => "2026-11-21",
  });
  return { server, store, sync, unauthorized };
}

/** Queue a placement plus a kitchen send and a cash payment for it, as the till would offline. */
async function queueOrder(sync: TillSync, orderId: string, seq: number, number = 1001) {
  await sync.record("push", null, null, () => placeEvent(TILL, placeBody(seq, orderId, [dosa(`${orderId}L1`)], number)));
  await sync.record("kitchen", orderId, null, (o) => kitchenEvent(TILL, o!, cmd(seq + 1)));
  return sync.record("push", orderId, null, (o) =>
    payEvent(TILL, o!, { ...cmd(seq + 2), payment_id: `${orderId}P`, method: "cash" }),
  );
}

describe("the till's outbox", () => {
  it("keeps taking orders offline and shows them as the server will", async () => {
    const { sync, store } = setup();
    const paid = await queueOrder(sync, "O1", 1);
    expect([paid.state, paid.balance_cents, paid.unsent_lines]).toEqual(["paid", 0, 0]);
    expect((await sync.pending("O1")).map((e) => [e.event.seq, e.kind])).toEqual([
      [1, "push"],
      [2, "kitchen"],
      [3, "push"],
    ]);
    expect(await store.order("O1")).toEqual(paid);
  });

  it("catches up once the server is back: placements first, kitchen sends through the route", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    await queueOrder(sync, "O2", 4, 1002);

    server.down = true;
    expect((await sync.drain()).reached).toBe(false);
    expect(await sync.pending()).toHaveLength(6);

    server.down = false;
    server.calls.length = 0;
    const report = await sync.drain();
    expect(report).toMatchObject({ reached: true, done: 6, failed: 0 });
    expect(server.calls).toEqual(["push 1,4", "kitchen 2", "push 3", "kitchen 5", "push 6", "get O1", "get O2"]);
    expect(await sync.pending()).toEqual([]);
    expect(report.notices.map((n) => n.text)).toEqual([
      "Order #1001: the kitchen ticket taken offline has printed",
      "Order #1002: the kitchen ticket taken offline has printed",
    ]);
    expect((await sync.store.order("O2"))?.state).toBe("paid");
  });

  it("is safe to push again: what already reached the server comes back as a duplicate", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    server.log.push(...(await sync.pending()).filter((e) => e.kind === "push").map((e) => e.event));
    server.kitchen = () => null;
    const report = await sync.drain();
    expect([report.done, report.failed]).toEqual([3, 0]);
    expect(report.notices.filter((n) => n.kind !== "info")).toEqual([]);
    expect(server.log.filter((e) => e.type === "order.paid")).toHaveLength(1);
  });

  it("flags a payment taken offline on an order another till had already settled", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    server.pushAck = (e) =>
      e.type === "order.paid" ? { status: "duplicate", detail: "already paid" } : null;
    const report = await sync.drain();
    expect(report.notices.map((n) => n.kind)).toContain("warn");
    expect(report.notices.find((n) => n.kind === "warn")?.text).toMatch(/already paid on another till/);

    // A plain retry of the same event stays quiet.
    const again = setup();
    await queueOrder(again.sync, "O1", 1);
    again.server.pushAck = (e) =>
      e.type === "order.paid" ? { status: "duplicate", detail: "event already in the log" } : null;
    expect((await again.sync.drain()).notices.filter((n) => n.kind === "warn")).toEqual([]);
  });

  it("holds back an order's changes when the server refuses its placement", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    await queueOrder(sync, "O2", 4, 1002);
    server.pushAck = (e) =>
      e.seq === 1 ? { status: "rejected", code: "order_number_out_of_range", detail: "order number 1001 is outside this device's range" } : null;
    const report = await sync.drain();
    expect([report.done, report.failed]).toEqual([3, 3]);
    expect(server.calls.filter((c) => c.startsWith("kitchen"))).toEqual(["kitchen 5"]);
    const failed = (await sync.store.entries()).filter((e) => e.status === "failed");
    expect(failed.map((e) => [e.event.seq, e.code])).toEqual([
      [1, "order_number_out_of_range"],
      [2, "order_not_synced"],
      [3, "order_not_synced"],
    ]);
    expect(report.notices[0]).toEqual({
      kind: "error",
      text: "Order #1001: order number 1001 is outside this device's range",
    });

    // A later drain doesn't retry them; discarding clears them.
    expect((await sync.drain()).done).toBe(0);
    for (const e of failed) await sync.discard(e.event.event_id);
    expect(await sync.store.entries()).toEqual([]);
  });

  it("takes fresh counters when the server has moved past them", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    server.lastSeq = 40;
    server.nextOrderNumber = 1005;
    let refused = false;
    server.pushAck = (e) => {
      if (e.seq === 1 && !refused) {
        refused = true;
        return { status: "rejected", code: "order_number_used", detail: "order number 1001 is already used" };
      }
      if (e.seq === 3) return { status: "rejected", code: "seq_conflict", detail: "seq 3 is already used" };
      return null;
    };
    const report = await sync.drain();
    expect(report).toMatchObject({ reached: true, failed: 0, done: 3 });
    const placedOnServer = server.log.find((e) => e.type === "order.placed")!;
    expect([placedOnServer.seq, placedOnServer.payload.order_number]).toEqual([100, 2000]);
    expect(server.log.find((e) => e.type === "order.paid")?.seq).toBe(101);
    expect((await sync.store.order("O1"))?.order_number).toBe(2000);
  });

  it("carries on past a kitchen printer that isn't answering, and tries it again later", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    server.kitchen = () => new ApiError(503, "printer_unavailable", "the kitchen printer isn't answering");
    const report = await sync.drain();
    expect([report.reached, report.done, report.failed]).toEqual([true, 2, 0]);
    const left = await sync.pending();
    expect(left.map((e) => [e.kind, e.attempts, e.code])).toEqual([["kitchen", 1, "printer_unavailable"]]);

    server.kitchen = () => null;
    expect((await sync.drain()).done).toBe(1);
    expect(await sync.pending()).toEqual([]);
  });

  it("repairs a kitchen send's seq from the server's answer", async () => {
    const { sync, server } = setup();
    await queueOrder(sync, "O1", 1);
    server.kitchen = (_id, body) =>
      body.seq === 2 ? new ApiError(409, "seq_conflict", "seq 2 is already used", { last_seq: 50 }) : null;
    expect((await sync.drain()).done).toBe(3);
    expect(server.calls.filter((c) => c.startsWith("kitchen"))).toEqual(["kitchen 2", "kitchen 100"]);
  });

  it("finds the one bad event when the server refuses a whole batch", async () => {
    const { sync, server } = setup();
    await sync.record("push", null, null, () => placeEvent(TILL, placeBody(1, "O1", [dosa("L1")])));
    await sync.record("push", null, null, () => placeEvent(TILL, placeBody(2, "O2", [dosa("L2")], 1002)));
    let batches = 0;
    const push = server.api.syncPush;
    server.api.syncPush = async (token, events) => {
      batches += 1;
      if (events.some((e) => e.seq === 2)) throw new ApiError(422, "invalid_request", "events.0.payload: bad");
      return push(token, events);
    };
    const report = await sync.drain();
    expect([batches, report.done, report.failed]).toEqual([3, 1, 1]);
  });

  it("unpairs a revoked till", async () => {
    const { sync, server, unauthorized } = setup();
    await queueOrder(sync, "O1", 1);
    server.pushAck = () => new ApiError(401, "invalid_token", "revoked");
    expect((await sync.drain()).reached).toBe(false);
    expect(unauthorized).toHaveBeenCalled();
  });

  it("folds unsynced changes onto a view fetched from the server", async () => {
    const { sync } = setup();
    const start = placed(1, "O1", [dosa("L1", 2)]);
    await sync.remember(start.order);
    await sync.record("push", "O1", null, (o) => addLinesEvent(TILL, o!, { ...cmd(2), lines: [dosa("L2")] }));
    const fresh = await sync.remember(start.order);
    expect([fresh.lines.length, fresh.total_cents]).toEqual([2, 5550]);
  });

  it("pulls the other tills' changes, page by page, and keeps its cursor", async () => {
    const { sync, server, store } = setup();
    const other = { ...TILL, deviceId: "01JDEV00000000000000000002" };
    const theirs = placeEvent(other, placeBody(9, "T1", [dosa("TL1")], 5001));
    const old = { ...placeEvent(other, placeBody(8, "T0", [dosa("TL0")], 5000)), business_date: "2026-11-10" };
    const paid = payEvent(other, applyEvent(null, theirs).order!, { ...cmd(10), payment_id: "TP", method: "cash", amount_cents: 500 });
    server.pull = [
      { events: [old, theirs], next_cursor: "2", has_more: true },
      { events: [{ ...paid, device_id: other.deviceId }], next_cursor: "3", has_more: false },
    ];
    const report = await sync.pull();
    expect([report.reached, report.events, report.touched]).toEqual([true, 3, ["T1"]]);
    expect(await store.meta(CURSOR_KEY)).toBe("3");
    expect(await store.order("T0")).toBeNull();
    expect((await store.order("T1"))?.balance_cents).toBe(1350);
    expect((await sync.openOrders()).map((o) => o.order_number)).toEqual([5001]);

    await sync.pull();
    expect(server.calls.at(-1)).toBe("pull 3");
  });

  it("never mints a seq or order number the outbox already holds", async () => {
    const { sync, store } = setup();
    await queueOrder(sync, "O1", 7, 1009);
    await store.saveEntry(newEntry({ ...cmdEvent(12) }, "push"));
    expect(queuedCounters(await store.entries())).toEqual({ lastSeq: 12, lastOrderNumber: 1009 });
  });
});

function cmdEvent(seq: number): SyncEvent {
  return {
    ...cmd(seq),
    occurred_at: "2026-11-21T00:00:00.000Z",
    device_id: TILL.deviceId,
    location_id: TILL.locationId,
    business_date: "2026-11-21",
    type: "order.voided",
    payload: { order_id: "O1" },
  };
}
