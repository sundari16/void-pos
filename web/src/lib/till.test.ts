import { describe, expect, it } from "vitest";
import {
  clearTill,
  loadTill,
  RangeExhausted,
  refreshTill,
  resyncSeq,
  saveTill,
  takeOrderNumber,
  takeSeq,
  tillFromPairing,
  type KeyValueStore,
} from "./till";
import type { PairResponse } from "./types";

function memoryStore(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

const pairing: PairResponse = {
  token: "secret",
  device: {
    id: "01JDEVICE00000000000000000",
    location_id: "01JLOCATION000000000000000",
    name: "Till 1",
    order_number_start: 1000,
    order_number_end: 1002,
    last_seq: 4,
    paired: true,
    paired_at: "2026-11-18T01:00:00Z",
    revoked_at: null,
  },
  location: {
    id: "01JLOCATION000000000000000",
    org_id: "01JORG00000000000000000000",
    name: "Brisbane CBD",
    timezone: "Australia/Brisbane",
  },
  next_order_number: 1001,
};

describe("till counters", () => {
  it("resume where the server says", () => {
    const till = tillFromPairing(pairing);
    expect(till.nextSeq).toBe(5);
    expect(till.nextOrderNumber).toBe(1001);
    expect(till.timezone).toBe("Australia/Brisbane");
  });

  it("hand out each seq and order number once", () => {
    const [seq, t1] = takeSeq(tillFromPairing(pairing));
    const [seq2] = takeSeq(t1);
    expect([seq, seq2]).toEqual([5, 6]);

    const [n1, t2] = takeOrderNumber(t1);
    const [n2, t3] = takeOrderNumber(t2);
    expect([n1, n2]).toEqual([1001, 1002]);
    expect(() => takeOrderNumber(t3)).toThrow(RangeExhausted);
  });

  it("only ever move forward", () => {
    const till = { ...tillFromPairing(pairing), nextSeq: 50, nextOrderNumber: 1002 };
    const refreshed = refreshTill(till, { ...pairing, next_order_number: 1001 });
    expect([refreshed.nextSeq, refreshed.nextOrderNumber]).toEqual([50, 1002]);
    expect(resyncSeq(till, 10).nextSeq).toBe(50);
    expect(resyncSeq(till, 80).nextSeq).toBe(81);
  });
});

describe("till storage", () => {
  it("round-trips and clears", () => {
    const store = memoryStore();
    const till = tillFromPairing(pairing);
    saveTill(store, till);
    expect(loadTill(store)).toEqual(till);
    clearTill(store);
    expect(loadTill(store)).toBeNull();
  });

  it("ignores missing, corrupt or foreign data", () => {
    const store = memoryStore();
    expect(loadTill(null)).toBeNull();
    expect(loadTill(store)).toBeNull();
    store.data.set("voidpos:till:v1", "{not json");
    expect(loadTill(store)).toBeNull();
    store.data.set("voidpos:till:v1", JSON.stringify({ token: "x", nextSeq: "1" }));
    expect(loadTill(store)).toBeNull();
  });
});
