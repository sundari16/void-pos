// The till's on-device store: the outbox of changes the server hasn't confirmed, the till's
// copy of recent orders (so it can carry on with them offline), and a few settings such as
// the pull cursor. sqliteStore.ts keeps it in expo-sqlite; MemoryStore here backs the tests
// and is the fallback where SQLite can't open (it lasts only while the app runs).

import type { OrderResponse, SyncEvent } from "../lib/types";
import { orderIdOf } from "./projection";

export interface OutboxEntry {
  event: SyncEvent;
  /** "push": sent through /sync/push. "kitchen": replayed through the send-to-kitchen
   *  route, so the ticket prints when the server is back. */
  kind: "push" | "kitchen";
  /** "failed": the server refused it; kept so the cashier sees it until they discard it. */
  status: "pending" | "failed";
  attempts: number;
  error: string | null;
  code: string | null;
}

export interface TillStore {
  /** Every outbox entry, in seq order. */
  entries(): Promise<OutboxEntry[]>;
  /** Insert or replace an entry (by event id). */
  saveEntry(entry: OutboxEntry): Promise<void>;
  removeEntries(eventIds: string[]): Promise<void>;
  order(id: string): Promise<OrderResponse | null>;
  orders(): Promise<OrderResponse[]>;
  saveOrders(orders: OrderResponse[]): Promise<void>;
  /** Queue an entry and save the order it changed, together. */
  record(entry: OutboxEntry, order: OrderResponse | null): Promise<void>;
  meta(key: string): Promise<string | null>;
  setMeta(key: string, value: string | null): Promise<void>;
  /** Forget orders from before `businessDate`, except those in `keep`. */
  pruneOrders(businessDate: string, keep: Set<string>): Promise<void>;
  clear(): Promise<void>;
}

export function entryOrderId(entry: OutboxEntry): string | null {
  return orderIdOf(entry.event);
}

export function newEntry(event: SyncEvent, kind: OutboxEntry["kind"]): OutboxEntry {
  return { event, kind, status: "pending", attempts: 0, error: null, code: null };
}

const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

export class MemoryStore implements TillStore {
  private outbox = new Map<string, OutboxEntry>();
  private views = new Map<string, OrderResponse>();
  private settings = new Map<string, string>();

  async entries(): Promise<OutboxEntry[]> {
    return [...this.outbox.values()].map(clone).sort((a, b) => a.event.seq - b.event.seq);
  }
  async saveEntry(entry: OutboxEntry): Promise<void> {
    this.outbox.set(entry.event.event_id, clone(entry));
  }
  async removeEntries(eventIds: string[]): Promise<void> {
    for (const id of eventIds) this.outbox.delete(id);
  }
  async order(id: string): Promise<OrderResponse | null> {
    const view = this.views.get(id);
    return view ? clone(view) : null;
  }
  async orders(): Promise<OrderResponse[]> {
    return [...this.views.values()].map(clone);
  }
  async saveOrders(orders: OrderResponse[]): Promise<void> {
    for (const order of orders) this.views.set(order.id, clone(order));
  }
  async record(entry: OutboxEntry, order: OrderResponse | null): Promise<void> {
    await this.saveEntry(entry);
    if (order) await this.saveOrders([order]);
  }
  async meta(key: string): Promise<string | null> {
    return this.settings.get(key) ?? null;
  }
  async setMeta(key: string, value: string | null): Promise<void> {
    if (value === null) this.settings.delete(key);
    else this.settings.set(key, value);
  }
  async pruneOrders(businessDate: string, keep: Set<string>): Promise<void> {
    for (const [id, view] of this.views) {
      if (view.business_date < businessDate && !keep.has(id)) this.views.delete(id);
    }
  }
  async clear(): Promise<void> {
    this.outbox.clear();
    this.views.clear();
    this.settings.clear();
  }
}
