import { ULID_PATTERN, encodeUlid, monotonicFactory, ulid } from "./ulid";

describe("ulid", () => {
  it("mints 26-char Crockford ULIDs", () => {
    const id = ulid();
    expect(id).toHaveLength(26);
    expect(ULID_PATTERN.test(id)).toBe(true);
  });

  it("is monotonic within a millisecond so ids sort in mint order", () => {
    const zeros = () => new Uint8Array(16);
    const next = monotonicFactory(zeros, () => 1_700_000_000_000);
    const a = next();
    const b = next();
    const c = next();
    expect(a < b).toBe(true);
    expect(b < c).toBe(true);
  });

  it("encodes the time into the first 10 characters", () => {
    const id = encodeUlid(0, new Uint8Array(16));
    expect(id.slice(0, 10)).toBe("0000000000");
    expect(id).toHaveLength(26);
  });
});
