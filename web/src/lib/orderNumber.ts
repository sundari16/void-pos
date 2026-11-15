// Per-device order numbering, tracked client-side in localStorage.
//
// The backend takes `order_number` from the client (devices own private ranges so two
// offline tills never collide). We mint the next number from a per-device counter
// seeded at NEXT_PUBLIC_ORDER_NUMBER_START and bump it only after a successful place.

import { config } from "./config";

function key(deviceId: string): string {
  return `voidpos:nextOrderNumber:${deviceId}`;
}

/** The order number the next placed order will carry (does not advance). */
export function peekOrderNumber(deviceId: string): number {
  if (typeof window === "undefined") return config.orderNumberStart;
  try {
    const raw = window.localStorage.getItem(key(deviceId));
    const n = raw ? Number(raw) : NaN;
    return Number.isFinite(n) ? n : config.orderNumberStart;
  } catch {
    return config.orderNumberStart;
  }
}

/** Advance the counter after an order has been placed successfully. */
export function advanceOrderNumber(deviceId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key(deviceId), String(peekOrderNumber(deviceId) + 1));
  } catch {
    // localStorage unavailable (private window etc.) — numbering just won't persist.
  }
}
