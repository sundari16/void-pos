// The back-office sign-in lives in sessionStorage only: closing the tab signs the admin
// out, and nothing about it reaches the till's local storage. Plus small display helpers
// shared by the back-office screens.

import type { AdminLocation } from "./adminTypes";

export const TOKEN_KEY = "voidpos.admin.token";
export const LOCATION_KEY = "voidpos.admin.location";

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function sessionStore(): KeyValueStore | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null; // storage blocked
  }
}

export interface SavedAdmin {
  token: string | null;
  locationId: string | null;
}

export function loadAdmin(store: KeyValueStore | null): SavedAdmin {
  return {
    token: store?.getItem(TOKEN_KEY) || null,
    locationId: store?.getItem(LOCATION_KEY) || null,
  };
}

export function saveToken(store: KeyValueStore | null, token: string): void {
  store?.setItem(TOKEN_KEY, token);
}

export function saveLocation(store: KeyValueStore | null, locationId: string): void {
  store?.setItem(LOCATION_KEY, locationId);
}

export function clearAdmin(store: KeyValueStore | null): void {
  store?.removeItem(TOKEN_KEY);
  store?.removeItem(LOCATION_KEY);
}

/** The location to open: the saved one if it still exists, the only one if there's just
 *  one, otherwise null (the admin picks). */
export function pickLocation(locations: AdminLocation[], savedId: string | null): string | null {
  if (savedId && locations.some((l) => l.id === savedId)) return savedId;
  return locations.length === 1 ? locations[0].id : null;
}

/** Whether an error from checking the token means the token itself is wrong. */
export function isBadToken(status: number, code: string): boolean {
  return status === 401 || code === "admin_disabled" || code === "admin_only";
}

/** What to tell the admin when the token they pasted was refused. */
export function signInError(status: number, code: string, detail: string): string {
  if (code === "admin_only") return "That's a till's token, not the admin token.";
  if (code === "admin_disabled") {
    return `Admin access is switched off on this server (${detail}).`;
  }
  if (status === 401) {
    return "That isn't the admin token. Check VOIDPOS_ADMIN_TOKEN on the venue server and paste it again.";
  }
  return detail;
}

// --- dates and times ---------------------------------------------------------------

/** Today in the location's own timezone as yyyy-mm-dd: business dates are venue-local. */
export function todayIn(timezone: string, now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** A server timestamp as the venue's local date and time, e.g. "19 Nov, 2:05 pm". */
export function formatDateTime(iso: string, timezone: string): string {
  return new Date(iso).toLocaleString("en-AU", {
    timeZone: timezone,
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** A server timestamp as the venue's local clock time, e.g. "2:05 pm". */
export function formatClock(iso: string, timezone: string): string {
  return new Date(iso).toLocaleTimeString("en-AU", {
    timeZone: timezone,
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Time left until `expiresAt` as "m:ss", or null once it has passed. */
export function timeLeft(expiresAt: string, now: Date = new Date()): string | null {
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (!(ms > 0)) return null;
  const total = Math.ceil(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** The file name for a location's export, e.g. "void-pos-brisbane-cbd-2026-11-19.json". */
export function exportFileName(locationName: string, date: string): string {
  const slug = locationName
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `void-pos-${slug || "location"}-${date}.json`;
}
