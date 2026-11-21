// The till's store in expo-sqlite, so queued changes and recent orders survive the app being
// closed or the tablet restarting while the server is out of reach. Rows hold JSON: the
// outbox entry, or the order as the till last knew it.

import * as SQLite from "expo-sqlite";
import type { OrderResponse } from "../lib/types";
import { entryOrderId, type OutboxEntry, type TillStore } from "./store";

const SCHEMA = `
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS outbox (
  event_id TEXT PRIMARY KEY NOT NULL,
  seq INTEGER NOT NULL,
  order_id TEXT,
  entry TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY NOT NULL,
  business_date TEXT NOT NULL,
  view TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY NOT NULL,
  value TEXT NOT NULL
);
`;

type Db = SQLite.SQLiteDatabase;

async function putEntry(db: Pick<Db, "runAsync">, entry: OutboxEntry): Promise<void> {
  await db.runAsync(
    "INSERT OR REPLACE INTO outbox (event_id, seq, order_id, entry) VALUES (?, ?, ?, ?)",
    entry.event.event_id,
    entry.event.seq,
    entryOrderId(entry),
    JSON.stringify(entry),
  );
}

async function putOrder(db: Pick<Db, "runAsync">, order: OrderResponse): Promise<void> {
  await db.runAsync(
    "INSERT OR REPLACE INTO orders (id, business_date, view) VALUES (?, ?, ?)",
    order.id,
    order.business_date,
    JSON.stringify(order),
  );
}

class SqliteStore implements TillStore {
  constructor(private readonly db: Db) {}

  async entries(): Promise<OutboxEntry[]> {
    const rows = await this.db.getAllAsync<{ entry: string }>("SELECT entry FROM outbox ORDER BY seq");
    return rows.map((r) => JSON.parse(r.entry) as OutboxEntry);
  }
  async saveEntry(entry: OutboxEntry): Promise<void> {
    await putEntry(this.db, entry);
  }
  async removeEntries(eventIds: string[]): Promise<void> {
    if (eventIds.length === 0) return;
    await this.db.withExclusiveTransactionAsync(async (txn) => {
      for (const id of eventIds) await txn.runAsync("DELETE FROM outbox WHERE event_id = ?", id);
    });
  }
  async order(id: string): Promise<OrderResponse | null> {
    const row = await this.db.getFirstAsync<{ view: string }>("SELECT view FROM orders WHERE id = ?", id);
    return row ? (JSON.parse(row.view) as OrderResponse) : null;
  }
  async orders(): Promise<OrderResponse[]> {
    const rows = await this.db.getAllAsync<{ view: string }>("SELECT view FROM orders");
    return rows.map((r) => JSON.parse(r.view) as OrderResponse);
  }
  async saveOrders(orders: OrderResponse[]): Promise<void> {
    if (orders.length === 0) return;
    await this.db.withExclusiveTransactionAsync(async (txn) => {
      for (const order of orders) await putOrder(txn, order);
    });
  }
  async record(entry: OutboxEntry, order: OrderResponse | null): Promise<void> {
    await this.db.withExclusiveTransactionAsync(async (txn) => {
      await putEntry(txn, entry);
      if (order) await putOrder(txn, order);
    });
  }
  async meta(key: string): Promise<string | null> {
    const row = await this.db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = ?", key);
    return row?.value ?? null;
  }
  async setMeta(key: string, value: string | null): Promise<void> {
    if (value === null) await this.db.runAsync("DELETE FROM meta WHERE key = ?", key);
    else await this.db.runAsync("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", key, value);
  }
  async pruneOrders(businessDate: string, keep: Set<string>): Promise<void> {
    const rows = await this.db.getAllAsync<{ id: string }>(
      "SELECT id FROM orders WHERE business_date < ?",
      businessDate,
    );
    const gone = rows.map((r) => r.id).filter((id) => !keep.has(id));
    if (gone.length === 0) return;
    await this.db.withExclusiveTransactionAsync(async (txn) => {
      for (const id of gone) await txn.runAsync("DELETE FROM orders WHERE id = ?", id);
    });
  }
  async clear(): Promise<void> {
    await this.db.execAsync("DELETE FROM outbox; DELETE FROM orders; DELETE FROM meta;");
  }
}

export async function openSqliteStore(name = "voidpos-till.db"): Promise<TillStore> {
  const db = await SQLite.openDatabaseAsync(name);
  await db.execAsync(SCHEMA);
  return new SqliteStore(db);
}
