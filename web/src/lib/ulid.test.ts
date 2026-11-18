import { describe, expect, it } from "vitest";
import { ULID_PATTERN, ulid } from "./ulid";

describe("ulid", () => {
  it("matches the server's id pattern", () => {
    for (let i = 0; i < 200; i += 1) expect(ulid()).toMatch(ULID_PATTERN);
  });

  it("encodes the time so ids sort by when they were minted", () => {
    const fixed = () => new Uint8Array(16);
    expect(ulid(0, fixed)).toBe("0".repeat(26));
    expect(ulid(1_700_000_000_000, fixed) < ulid(1_700_000_000_001, fixed)).toBe(true);
  });

  it("uses all 80 random bits", () => {
    const top = () => new Uint8Array(16).fill(31);
    expect(ulid(0, top).slice(10)).toBe("Z".repeat(16));
  });

  it("refuses times it can't encode", () => {
    expect(() => ulid(-1)).toThrow(RangeError);
    expect(() => ulid(2 ** 48)).toThrow(RangeError);
    expect(() => ulid(1.5)).toThrow(RangeError);
  });
});
