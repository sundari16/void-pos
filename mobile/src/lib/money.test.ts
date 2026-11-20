import { formatCents, formatDelta } from "./money";

describe("formatCents", () => {
  it("formats integer cents as AUD, display only", () => {
    expect(formatCents(1850)).toBe("$18.50");
    expect(formatCents(0)).toBe("$0.00");
    expect(formatCents(5)).toBe("$0.05");
    expect(formatCents(100000)).toBe("$1,000.00");
    expect(formatCents(-500)).toBe("-$5.00");
  });

  it("prefixes a non-AUD currency with its code", () => {
    expect(formatCents(1000, "USD")).toBe("USD $10.00");
  });
});

describe("formatDelta", () => {
  it("signs a modifier delta, and is empty at zero", () => {
    expect(formatDelta(150)).toBe("+$1.50");
    expect(formatDelta(-50)).toBe("-$0.50");
    expect(formatDelta(0)).toBe("");
  });
});
