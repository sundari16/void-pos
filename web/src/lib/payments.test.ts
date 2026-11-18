import { describe, expect, it } from "vitest";
import { MAX_TERMINAL_REF, providerLabel, terminalRef } from "./payments";

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
});
