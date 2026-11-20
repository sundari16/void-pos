import { centsToInput, evenSplit, parseDollars, terminalRef } from "./payments";

describe("parseDollars", () => {
  it("reads a dollar amount into integer cents", () => {
    expect(parseDollars("12.50")).toEqual({ cents: 1250 });
    expect(parseDollars("$1,000")).toEqual({ cents: 100000 });
    expect(parseDollars("5")).toEqual({ cents: 500 });
    expect(parseDollars("0.9")).toEqual({ cents: 90 });
  });

  it("rejects what isn't a plain amount", () => {
    expect(parseDollars("12.345")).toHaveProperty("error");
    expect(parseDollars("abc")).toHaveProperty("error");
    expect(parseDollars("")).toHaveProperty("error");
  });
});

describe("evenSplit", () => {
  it("splits in whole cents, odd cents to the first shares", () => {
    expect(evenSplit(1000, 3)).toEqual([334, 333, 333]);
    expect(evenSplit(1000, 1)).toEqual([1000]);
    expect(evenSplit(1001, 2)).toEqual([501, 500]);
  });
});

describe("centsToInput / terminalRef", () => {
  it("renders cents as a typed amount", () => {
    expect(centsToInput(1250)).toBe("12.50");
  });

  it("requires a non-empty terminal reference", () => {
    expect(terminalRef("  A123 ")).toEqual({ ref: "A123" });
    expect(terminalRef("   ")).toHaveProperty("error");
  });
});
