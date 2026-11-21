// The till's sync engine: queue changes made while the server is out of reach, push them
// through /sync/push once it's back, and pull the other tills' changes from /sync/pull so
// this till can carry on with any recent order even while offline.
//
// Pushing is safe to repeat: every event carries the id and seq the till minted for it, so
// one that already reached the server (the response was lost, or the till retried) comes back
// as a duplicate. Placements go first, so an order is on the server before anything that
// changes it; if a placement is refused, that order's later changes are held back rather than
// pushed to an order the server doesn't have. Kitchen sends are replayed through the
// send-to-kitchen route, which prints the ticket. A refused event stays in the outbox, marked
// failed, until the cashier has seen it.

import { ApiError } from "../lib/api";
import type {
  CommandIn,
  DeviceSession,
  KitchenResponse,
  OrderResponse,
  PullResponse,
  SyncAck,
  SyncEvent,
} from "../lib/types";
import { dayBefore, type TillIdentity } from "./events";
import { applyAll, applyEvent, orderIdOf } from "./projection";
import { entryOrderId, newEntry, type OutboxEntry, type TillStore } from "./store";

export const MAX_PUSH = 500;
export const PULL_PAGE = 500;
const MAX_PULL_PAGES = 40;
export const CURSOR_KEY = "pull_cursor";

export interface SyncApi {
  syncPush(token: string, events: SyncEvent[]): Promise<{ acks: SyncAck[] }>;
  syncPull(token: string, cursor: string | null, limit?: number): Promise<PullResponse>;
  sendToKitchen(token: string, orderId: string, body: CommandIn): Promise<KitchenResponse>;
  getOrder(token: string, orderId: string): Promise<OrderResponse>;
  me(token: string): Promise<DeviceSession>;
}

export interface SyncTill extends TillIdentity {
  token: string;
}

export interface SyncDeps {
  store: TillStore;
  api: SyncApi;
  till: () => SyncTill | null;
  /** Carry the till's seq on past the server's last one; returns a fresh seq. */
  seqAfter: (lastSeq: number) => number;
  /** Carry the till's order numbers on from the server's next free one; returns one. */
  orderNumberAfter: (next: number) => number;
  onUnauthorized: () => void;
  /** Today's business date at the location. */
  today: () => string;
}

export interface Notice {
  kind: "info" | "warn" | "error";
  text: string;
}

export interface DrainReport {
  /** False when the server couldn't be reached (or answered 5xx): try again later. */
  reached: boolean;
  done: number;
  failed: number;
  /** Orders whose view changed (refreshed from the server or a refusal). */
  touched: string[];
  notices: Notice[];
}

export interface PullReport {
  reached: boolean;
  events: number;
  touched: string[];
}

const DONE = new Set(["applied", "duplicate", "ignored", "conflict"]);

export function isNetworkError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 0 && err.code === "network_error";
}

function unreachable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || err.status >= 500);
}

/** The highest seq and order number the outbox holds, so the till never mints them again
 *  (its counters live apart from the outbox and could have lost a write). */
export function queuedCounters(entries: OutboxEntry[]): { lastSeq: number; lastOrderNumber: number } {
  let lastSeq = 0;
  let lastOrderNumber = 0;
  for (const { event } of entries) {
    lastSeq = Math.max(lastSeq, event.seq);
    if (event.type === "order.placed") {
      lastOrderNumber = Math.max(lastOrderNumber, Number(event.payload.order_number) || 0);
    }
  }
  return { lastSeq, lastOrderNumber };
}

export class TillSync {
  private chain: Promise<unknown> = Promise.resolve();
  private draining: Promise<DrainReport> | null = null;
  private pulling: Promise<PullReport> | null = null;

  constructor(private readonly deps: SyncDeps) {}

  /** Run store read-modify-writes one at a time (never across a network call). */
  private exclusive<T>(task: () => Promise<T>): Promise<T> {
    const run = this.chain.then(task, task);
    this.chain = run.catch(() => undefined);
    return run;
  }

  get store(): TillStore {
    return this.deps.store;
  }

  async pending(orderId?: string): Promise<OutboxEntry[]> {
    const all = (await this.deps.store.entries()).filter((e) => e.status === "pending");
    return orderId === undefined ? all : all.filter((e) => entryOrderId(e) === orderId);
  }

  /** Queue a change: build its event from the till's latest copy of the order, fold it in,
   *  and save both together. Returns the order as it now stands on this till. */
  record(
    kind: OutboxEntry["kind"],
    orderId: string | null,
    fallback: OrderResponse | null,
    build: (order: OrderResponse | null) => SyncEvent,
  ): Promise<OrderResponse> {
    return this.exclusive(async () => {
      const base = orderId ? ((await this.deps.store.order(orderId)) ?? fallback) : null;
      const event = build(base);
      const next = applyEvent(base, event).order;
      if (!next) throw new Error("that change has no order to apply to");
      await this.deps.store.record(newEntry(event, kind), next);
      return next;
    });
  }

  /** Keep the server's view of an order, with this till's unsynced changes folded on top. */
  remember(order: OrderResponse): Promise<OrderResponse> {
    return this.exclusive(async () => {
      const queued = await this.pending(order.id);
      const view = applyAll(order, queued.map((e) => e.event)) ?? order;
      await this.deps.store.saveOrders([view]);
      return view;
    });
  }

  /** Forget a refused entry the cashier has seen. */
  discard(eventId: string): Promise<void> {
    return this.exclusive(() => this.deps.store.removeEntries([eventId]));
  }

  drain(): Promise<DrainReport> {
    this.draining ??= this.runDrain().finally(() => {
      this.draining = null;
    });
    return this.draining;
  }

  pull(): Promise<PullReport> {
    this.pulling ??= this.runPull().finally(() => {
      this.pulling = null;
    });
    return this.pulling;
  }

  private async label(orderId: string | null): Promise<string> {
    const order = orderId ? await this.deps.store.order(orderId) : null;
    return order ? `Order #${order.order_number}` : "A change";
  }

  private async runDrain(): Promise<DrainReport> {
    const report: DrainReport = { reached: true, done: 0, failed: 0, touched: [], notices: [] };
    const till = this.deps.till();
    if (!till) return { ...report, reached: false };
    const touched = new Set<string>();

    const fail = async (entry: OutboxEntry, error: string, code: string | null) => {
      const orderId = entryOrderId(entry);
      await this.deps.store.saveEntry({ ...entry, status: "failed", error, code, attempts: entry.attempts + 1 });
      report.failed += 1;
      report.notices.push({ kind: "error", text: `${await this.label(orderId)}: ${error}` });
      if (orderId) touched.add(orderId);
    };
    const done = async (entries: OutboxEntry[]) => {
      await this.exclusive(() => this.deps.store.removeEntries(entries.map((e) => e.event.event_id)));
      report.done += entries.length;
      for (const e of entries) {
        const id = entryOrderId(e);
        if (id) touched.add(id);
      }
    };

    /** Orders whose placement the server refused: their other changes can't go. */
    const blocked = async (): Promise<Set<string>> =>
      new Set(
        (await this.deps.store.entries())
          .filter((e) => e.status === "failed" && e.event.type === "order.placed")
          .map((e) => entryOrderId(e) ?? ""),
      );

    const pushAll = async (entries: OutboxEntry[], attempt = 0): Promise<boolean> => {
      if (entries.length === 0) return true;
      let acks: SyncAck[];
      try {
        acks = (await this.deps.api.syncPush(till.token, entries.map((e) => e.event))).acks;
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          this.deps.onUnauthorized();
          return false;
        }
        if (unreachable(err)) return false;
        // The batch as a whole was refused (a malformed event): find which, one at a time.
        if (entries.length > 1) {
          for (const entry of entries) if (!(await pushAll([entry], attempt))) return false;
          return true;
        }
        const e = err instanceof ApiError ? err : null;
        await fail(entries[0], e?.detail ?? String(err), e?.code ?? null);
        return true;
      }

      const byId = new Map(entries.map((e) => [e.event.event_id, e]));
      const finished: OutboxEntry[] = [];
      const reseq: OutboxEntry[] = [];
      const renumber: OutboxEntry[] = [];
      for (const ack of acks) {
        const entry = byId.get(ack.event_id);
        if (!entry) continue;
        if (DONE.has(ack.status)) {
          finished.push(entry);
          if (ack.status === "conflict" || ack.status === "ignored") {
            report.notices.push({
              kind: "warn",
              text: `${await this.label(entryOrderId(entry))}: ${ack.detail ?? ack.status} (changed on another till)`,
            });
          }
        } else if (ack.code === "seq_conflict" && attempt === 0) {
          reseq.push(entry);
        } else if (ack.code === "order_number_used" && attempt === 0) {
          renumber.push(entry);
        } else {
          await fail(entry, ack.detail ?? "the server refused this change", ack.code);
        }
      }
      await done(finished);
      if (reseq.length === 0 && renumber.length === 0) return true;

      // The till's counters fell behind the server's: take fresh ones and push those again.
      let session: DeviceSession;
      try {
        session = await this.deps.api.me(till.token);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) this.deps.onUnauthorized();
        return false;
      }
      const retry: OutboxEntry[] = [];
      for (const entry of [...reseq, ...renumber]) {
        const event: SyncEvent = { ...entry.event, seq: this.deps.seqAfter(session.device.last_seq) };
        if (renumber.includes(entry)) {
          const number = this.deps.orderNumberAfter(session.next_order_number);
          event.payload = { ...event.payload, order_number: number };
          await this.renumber(entryOrderId(entry), number);
        }
        const fixed = { ...entry, event };
        await this.exclusive(async () => {
          await this.deps.store.removeEntries([entry.event.event_id]);
          await this.deps.store.saveEntry(fixed);
        });
        retry.push(fixed);
      }
      return pushAll(retry, attempt + 1);
    };

    const kitchen = async (entry: OutboxEntry, attempt = 0): Promise<boolean> => {
      const orderId = entryOrderId(entry) ?? "";
      const { event_id, seq, occurred_at } = entry.event;
      try {
        const sent = await this.deps.api.sendToKitchen(till.token, orderId, { event_id, seq, occurred_at });
        await done([entry]);
        if (!sent.replayed) {
          report.notices.push({
            kind: "info",
            text: `${await this.label(orderId)}: ${
              sent.printed ? "the kitchen ticket taken offline has printed" : "marked sent (no kitchen printer is set up)"
            }`,
          });
        }
        return true;
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        if (err.status === 401) {
          this.deps.onUnauthorized();
          return false;
        }
        if (err.status === 0) return false;
        const lastSeq = err.body.last_seq;
        if (err.code === "seq_conflict" && typeof lastSeq === "number" && attempt === 0) {
          const fixed = { ...entry, event: { ...entry.event, seq: this.deps.seqAfter(lastSeq) } };
          await this.deps.store.saveEntry(fixed);
          return kitchen(fixed, attempt + 1);
        }
        if (err.status >= 500) {
          // The printer is off, say: keep it for next time and carry on with the rest.
          await this.deps.store.saveEntry({ ...entry, attempts: entry.attempts + 1, error: err.detail, code: err.code });
          return true;
        }
        if (err.code === "order_voided") {
          await done([entry]);
          report.notices.push({ kind: "warn", text: `${await this.label(orderId)} was voided; its kitchen ticket wasn't printed` });
          return true;
        }
        await fail(entry, err.detail, err.code);
        return true;
      }
    };

    const holdBack = async (entries: OutboxEntry[]): Promise<OutboxEntry[]> => {
      const stuck = await blocked();
      const go: OutboxEntry[] = [];
      for (const entry of entries) {
        if (stuck.has(entryOrderId(entry) ?? "")) await fail(entry, "its order didn't reach the server", "order_not_synced");
        else go.push(entry);
      }
      return go;
    };

    // 1. Placements, so every order is on the server before what changes it.
    const placements = (await this.pending()).filter((e) => e.kind === "push" && e.event.type === "order.placed");
    for (let i = 0; i < placements.length && report.reached; i += MAX_PUSH) {
      report.reached = await pushAll(placements.slice(i, i + MAX_PUSH));
    }

    // 2. Everything else in the order it happened; kitchen sends one at a time between pushes.
    if (report.reached) {
      const rest = await holdBack((await this.pending()).filter((e) => e.event.type !== "order.placed" || e.kind !== "push"));
      let run: OutboxEntry[] = [];
      const flush = async () => {
        const batch = run;
        run = [];
        for (let i = 0; i < batch.length; i += MAX_PUSH) {
          if (!(await pushAll(batch.slice(i, i + MAX_PUSH)))) return false;
        }
        return true;
      };
      for (const entry of rest) {
        if (entry.kind === "push") {
          run.push(entry);
          continue;
        }
        if (!(await flush()) || !(await kitchen(entry))) {
          report.reached = false;
          break;
        }
      }
      if (report.reached && !(await flush())) report.reached = false;
    }

    // 3. Take the server's word for every order that has nothing left to send.
    const left = new Set((await this.pending()).map(entryOrderId));
    for (const orderId of touched) {
      if (left.has(orderId) || !report.reached) continue;
      try {
        await this.remember(await this.deps.api.getOrder(till.token, orderId));
      } catch {
        // Keep the till's own view; the next sync refreshes it.
      }
    }
    report.touched = [...touched];
    return report;
  }

  private renumber(orderId: string | null, orderNumber: number): Promise<void> {
    return this.exclusive(async () => {
      const order = orderId ? await this.deps.store.order(orderId) : null;
      if (order) await this.deps.store.saveOrders([{ ...order, order_number: orderNumber }]);
    });
  }

  private async runPull(): Promise<PullReport> {
    const report: PullReport = { reached: true, events: 0, touched: [] };
    const till = this.deps.till();
    if (!till) return { ...report, reached: false };
    const touched = new Set<string>();
    const cutoff = dayBefore(this.deps.today());
    let cursor = await this.deps.store.meta(CURSOR_KEY);
    for (let page = 0; page < MAX_PULL_PAGES; page += 1) {
      let res: PullResponse;
      try {
        res = await this.deps.api.syncPull(till.token, cursor, PULL_PAGE);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) this.deps.onUnauthorized();
        return { ...report, reached: false, touched: [...touched] };
      }
      await this.exclusive(async () => {
        const changed = new Map<string, OrderResponse>();
        for (const event of res.events) {
          const orderId = orderIdOf(event);
          // Older days are history: step past them, but the till only carries recent orders.
          if (!orderId || event.business_date < cutoff) continue;
          const base = changed.get(orderId) ?? (await this.deps.store.order(orderId));
          const next = applyEvent(base, event).order;
          if (next && next !== base) changed.set(orderId, next);
        }
        await this.deps.store.saveOrders([...changed.values()]);
        for (const id of changed.keys()) touched.add(id);
        await this.deps.store.setMeta(CURSOR_KEY, res.next_cursor);
      });
      report.events += res.events.length;
      cursor = res.next_cursor;
      if (!res.has_more) break;
    }
    report.touched = [...touched];
    return report;
  }

  /** Orders the till can pick back up: unsettled, from today or yesterday. */
  async openOrders(): Promise<OrderResponse[]> {
    const cutoff = dayBefore(this.deps.today());
    return (await this.deps.store.orders())
      .filter((o) => (o.state === "open" || o.state === "sent") && o.business_date >= cutoff)
      .sort((a, b) => (a.id < b.id ? 1 : -1));
  }

  /** Forget orders older than yesterday that have nothing queued. */
  async prune(): Promise<void> {
    const keep = new Set((await this.deps.store.entries()).map((e) => entryOrderId(e) ?? ""));
    await this.exclusive(() => this.deps.store.pruneOrders(dayBefore(this.deps.today()), keep));
  }
}
