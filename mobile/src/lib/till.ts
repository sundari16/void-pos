// This till's identity and counters.
//
// Pairing gives the till a bearer token, its location, its order-number range and where its
// counters resume. Every command takes the next `seq`, and every order the next order
// number; both are written back before the request goes out, so a crash mid-request can skip
// a number but never reuse one. These functions are pure: callers persist the result (see
// storage.ts, which keeps the Till in the device's AsyncStorage).

import type { DeviceSession, PairResponse } from "./types";

export interface Till {
  token: string;
  deviceId: string;
  deviceName: string;
  locationId: string;
  locationName: string;
  timezone: string;
  /** The public-holiday surcharge the till offers (1500 = 15%), if the venue set one. */
  holidaySurchargeBp?: number | null;
  orderNumberStart: number;
  orderNumberEnd: number;
  nextSeq: number;
  nextOrderNumber: number;
}

export const TILL_KEY = "voidpos:till:v1";

export class RangeExhausted extends Error {
  constructor(till: Till) {
    super(
      `This till has used all of its order numbers (${till.orderNumberStart}-${till.orderNumberEnd}). ` +
        "Ask the venue admin to register it again with a new range.",
    );
    this.name = "RangeExhausted";
  }
}

function fromSession(session: DeviceSession) {
  return {
    deviceId: session.device.id,
    deviceName: session.device.name,
    locationId: session.location.id,
    locationName: session.location.name,
    timezone: session.location.timezone,
    holidaySurchargeBp: session.location.holiday_surcharge_bp ?? null,
    orderNumberStart: session.device.order_number_start,
    orderNumberEnd: session.device.order_number_end,
  };
}

export function tillFromPairing(pairing: PairResponse): Till {
  return {
    token: pairing.token,
    ...fromSession(pairing),
    nextSeq: pairing.device.last_seq + 1,
    nextOrderNumber: pairing.next_order_number,
  };
}

/** Merge what the server knows; counters only ever move forward. */
export function refreshTill(till: Till, session: DeviceSession): Till {
  return {
    ...till,
    ...fromSession(session),
    nextSeq: Math.max(till.nextSeq, session.device.last_seq + 1),
    nextOrderNumber: Math.max(till.nextOrderNumber, session.next_order_number),
  };
}

export function takeSeq(till: Till): [number, Till] {
  return [till.nextSeq, { ...till, nextSeq: till.nextSeq + 1 }];
}

export function takeOrderNumber(till: Till): [number, Till] {
  if (till.nextOrderNumber > till.orderNumberEnd) throw new RangeExhausted(till);
  return [till.nextOrderNumber, { ...till, nextOrderNumber: till.nextOrderNumber + 1 }];
}

/** After a seq_conflict: carry on from the server's last applied seq. */
export function resyncSeq(till: Till, lastSeq: number): Till {
  return { ...till, nextSeq: Math.max(till.nextSeq, lastSeq + 1) };
}

/** After order_number_used: carry on from the server's next free number. */
export function resyncOrderNumber(till: Till, next: number): Till {
  return { ...till, nextOrderNumber: Math.max(till.nextOrderNumber, next) };
}

function isTill(value: unknown): value is Till {
  if (typeof value !== "object" || value === null) return false;
  const t = value as Record<string, unknown>;
  const strings = ["token", "deviceId", "deviceName", "locationId", "locationName", "timezone"];
  const numbers = ["orderNumberStart", "orderNumberEnd", "nextSeq", "nextOrderNumber"];
  return (
    strings.every((k) => typeof t[k] === "string" && t[k] !== "") &&
    numbers.every((k) => Number.isInteger(t[k]))
  );
}

/** Parse a stored JSON string back into a Till, or null if it isn't one. */
export function parseTill(raw: string | null): Till | null {
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    return isTill(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function serializeTill(till: Till): string {
  return JSON.stringify(till);
}
