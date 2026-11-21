// Generated from web/src/lib/ulid.test.ts by mobile/scripts/sync-lib.mjs — edit the web copy.
import { encodeUlid, monotonicFactory, ULID_PATTERN, ulid } from "./ulid";

describe("ulid", () => {
  it("matches the server's id pattern", () => {
    for (let i = 0; i < 200; i += 1) expect(ulid()).toMatch(ULID_PATTERN);
  });

  it("encodes the time so ids sort by when they were minted", () => {
    const zeros = new Uint8Array(16);
    expect(encodeUlid(0, zeros)).toBe("0".repeat(26));
    expect(encodeUlid(1_700_000_000_000, zeros) < encodeUlid(1_700_000_000_001, zeros)).toBe(true);
  });

  it("uses all 80 random bits", () => {
    expect(encodeUlid(0, new Uint8Array(16).fill(31)).slice(10)).toBe("Z".repeat(16));
  });

  it("refuses times it can't encode", () => {
    const bytes = new Uint8Array(16);
    expect(() => encodeUlid(-1, bytes)).toThrow(RangeError);
    expect(() => encodeUlid(2 ** 48, bytes)).toThrow(RangeError);
    expect(() => encodeUlid(1.5, bytes)).toThrow(RangeError);
  });

  it("counts up within a millisecond, and when the clock steps back", () => {
    let now = 1_700_000_000_000;
    const next = monotonicFactory(() => new Uint8Array(16).fill(30), () => now);
    const first = next();
    const second = next();
    expect(first.slice(10)).toBe("Y".repeat(16));
    expect(second.slice(10)).toBe("Y".repeat(15) + "Z");
    expect(next().slice(10)).toBe("Y".repeat(14) + "Z0"); // carries
    now -= 5;
    const stepped = next();
    expect(stepped > second).toBe(true);
    expect(stepped.slice(0, 10)).toBe(first.slice(0, 10));
  });

  it("mints in order even when a millisecond is shared", () => {
    const ids = Array.from({ length: 500 }, () => ulid());
    expect([...ids].sort()).toEqual(ids);
  });
});
