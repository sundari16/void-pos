import {
  parseTill,
  RangeExhausted,
  refreshTill,
  resyncSeq,
  serializeTill,
  takeOrderNumber,
  takeSeq,
  tillFromPairing,
} from "./till";
import type { DeviceSession, PairResponse } from "./types";

function pairing(): PairResponse {
  return {
    token: "tok",
    device: {
      id: "d",
      location_id: "loc",
      name: "Till 1",
      order_number_start: 100,
      order_number_end: 102,
      last_seq: 4,
      paired: true,
      paired_at: null,
      revoked_at: null,
    },
    location: {
      id: "loc",
      org_id: "o",
      name: "Madras Curry House",
      timezone: "Australia/Brisbane",
      holiday_surcharge_bp: 1500,
    },
    next_order_number: 100,
  };
}

describe("tillFromPairing", () => {
  it("seeds the counters from the paired device", () => {
    const till = tillFromPairing(pairing());
    expect(till.token).toBe("tok");
    expect(till.nextSeq).toBe(5); // last_seq + 1
    expect(till.nextOrderNumber).toBe(100);
    expect(till.orderNumberEnd).toBe(102);
    expect(till.holidaySurchargeBp).toBe(1500);
  });
});

describe("counters", () => {
  it("hand out seq and order numbers, moving forward each time", () => {
    const till = tillFromPairing(pairing());
    const [seq, afterSeq] = takeSeq(till);
    expect(seq).toBe(5);
    expect(afterSeq.nextSeq).toBe(6);

    const [n1, a1] = takeOrderNumber(till);
    const [n2, a2] = takeOrderNumber(a1);
    const [n3, a3] = takeOrderNumber(a2);
    expect([n1, n2, n3]).toEqual([100, 101, 102]);
    expect(() => takeOrderNumber(a3)).toThrow(RangeExhausted);
  });

  it("resyncSeq only ever moves the seq forward", () => {
    const till = tillFromPairing(pairing());
    expect(resyncSeq(till, 10).nextSeq).toBe(11);
    expect(resyncSeq(till, 2).nextSeq).toBe(till.nextSeq); // behind: unchanged
  });
});

describe("refreshTill", () => {
  it("takes the further of each counter from the server", () => {
    const till = tillFromPairing(pairing());
    const session: DeviceSession = {
      device: { ...pairing().device, last_seq: 20 },
      location: pairing().location,
      next_order_number: 108,
    };
    const refreshed = refreshTill(till, session);
    expect(refreshed.nextSeq).toBe(21);
    expect(refreshed.nextOrderNumber).toBe(108);
  });
});

describe("parseTill / serializeTill", () => {
  it("round-trips a till and rejects junk", () => {
    const till = tillFromPairing(pairing());
    expect(parseTill(serializeTill(till))).toEqual(till);
    expect(parseTill("not json")).toBeNull();
    expect(parseTill(null)).toBeNull();
    expect(parseTill(JSON.stringify({ token: "x" }))).toBeNull();
  });
});
