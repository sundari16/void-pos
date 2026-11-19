import { describe, expect, it } from "vitest";
import {
  clearAdmin,
  exportFileName,
  isBadToken,
  loadAdmin,
  pickLocation,
  saveLocation,
  saveToken,
  signInError,
  timeLeft,
  todayIn,
  type KeyValueStore,
} from "./adminSession";
import type { AdminLocation } from "./adminTypes";

function memoryStore(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k) => data.get(k) ?? null,
    setItem: (k, v) => void data.set(k, v),
    removeItem: (k) => void data.delete(k),
  };
}

const loc = (id: string) => ({ id, name: id }) as AdminLocation;

describe("admin session", () => {
  it("keeps the token and location, and signing out clears both", () => {
    const store = memoryStore();
    expect(loadAdmin(store)).toEqual({ token: null, locationId: null });
    saveToken(store, "secret-admin-token-0123456789");
    saveLocation(store, "LOC1");
    expect(loadAdmin(store)).toEqual({ token: "secret-admin-token-0123456789", locationId: "LOC1" });
    clearAdmin(store);
    expect(store.data.size).toBe(0);
  });

  it("copes without storage", () => {
    expect(loadAdmin(null)).toEqual({ token: null, locationId: null });
    expect(() => saveToken(null, "t")).not.toThrow();
  });

  it("opens the saved location, or the only one, or asks", () => {
    expect(pickLocation([loc("A"), loc("B")], "B")).toBe("B");
    expect(pickLocation([loc("A"), loc("B")], "GONE")).toBeNull();
    expect(pickLocation([loc("A")], null)).toBe("A");
    expect(pickLocation([], null)).toBeNull();
  });

  it("treats 401 and admin refusals as a wrong token", () => {
    expect(isBadToken(401, "invalid_token")).toBe(true);
    expect(isBadToken(403, "admin_disabled")).toBe(true);
    expect(isBadToken(403, "admin_only")).toBe(true);
    expect(isBadToken(0, "network_error")).toBe(false);
    expect(isBadToken(500, "internal_error")).toBe(false);
  });

  it("explains a refused token", () => {
    expect(signInError(403, "admin_only", "this action needs the admin token")).toMatch(/till's token/);
    expect(signInError(403, "admin_disabled", "admin access is off")).toMatch(/switched off.*admin access is off/);
    expect(signInError(401, "invalid_token", "that token isn't valid")).toMatch(/isn't the admin token/);
    expect(signInError(500, "internal_error", "internal error")).toBe("internal error");
  });
});

describe("dates and times", () => {
  it("takes today in the venue's timezone", () => {
    // 22:30 UTC on the 18th is 08:30 on the 19th in Brisbane.
    const now = new Date("2026-11-18T22:30:00Z");
    expect(todayIn("Australia/Brisbane", now)).toBe("2026-11-19");
    expect(todayIn("UTC", now)).toBe("2026-11-18");
  });

  it("counts down to a pairing code's expiry", () => {
    const now = new Date("2026-11-19T00:00:00Z");
    expect(timeLeft("2026-11-19T00:15:00Z", now)).toBe("15:00");
    expect(timeLeft("2026-11-19T00:00:05.200Z", now)).toBe("0:06");
    expect(timeLeft("2026-11-19T00:00:00Z", now)).toBeNull();
    expect(timeLeft("not a date", now)).toBeNull();
  });

  it("names an export file after the location and date", () => {
    expect(exportFileName("Brisbane CBD", "2026-11-19")).toBe("void-pos-brisbane-cbd-2026-11-19.json");
    expect(exportFileName("!!", "2026-11-19")).toBe("void-pos-location-2026-11-19.json");
  });
});
