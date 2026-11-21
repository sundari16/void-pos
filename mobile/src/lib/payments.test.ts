// Generated from web/src/lib/payments.test.ts by mobile/scripts/sync-lib.mjs — edit the web copy.
import {
  centsToInput,
  evenSplit,
  MAX_TERMINAL_REF,
  parseDollars,
  providerLabel,
  terminalRef,
} from "./payments";

describe("payments", () => {
  it("names providers for people", () => {
    expect(providerLabel("cash")).toBe("Cash");
    expect(providerLabel("external")).toBe("Card on terminal");
    expect(providerLabel("someday")).toBe("someday");
  });

  it("needs a terminal reference for a card payment", () => {
    expect(terminalRef("  0042 ")).toEqual({ ref: "0042" });
    expect(terminalRef("   ")).toHaveProperty("error");
    expect(terminalRef("x".repeat(MAX_TERMINAL_REF + 1))).toHaveProperty("error");
  });

  it("splits a bill evenly in whole cents", () => {
    expect(evenSplit(1000, 3)).toEqual([334, 333, 333]);
    expect(evenSplit(3700, 2)).toEqual([1850, 1850]);
    expect(evenSplit(5, 4)).toEqual([2, 1, 1, 1]);
    expect(evenSplit(3700, 0)).toEqual([3700]);
    expect(evenSplit(1001, 7).reduce((a, b) => a + b, 0)).toBe(1001);
  });

  it("reads amounts the cashier types", () => {
    expect(parseDollars("12.5")).toEqual({ cents: 1250 });
    expect(parseDollars(" $1,234.05 ")).toEqual({ cents: 123405 });
    expect(parseDollars("7")).toEqual({ cents: 700 });
    expect(parseDollars("1.234")).toHaveProperty("error");
    expect(parseDollars("-3")).toHaveProperty("error");
    expect(parseDollars("")).toHaveProperty("error");
    expect(centsToInput(1850)).toBe("18.50");
  });
});
