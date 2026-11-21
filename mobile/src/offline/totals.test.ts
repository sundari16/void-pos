import { orderMath, rateRatio, roundHalfUp, type MathAdjustment, type MathLine } from "./totals";

const line = (id: string, parent: string | null, quantity: number, cents: number, rate: number): MathLine => ({
  id,
  parent_line_id: parent,
  quantity,
  unit_price_cents: cents,
  tax_rate: rate,
});
const adj = (id: string, kind: MathAdjustment["kind"], basis: MathAdjustment["basis"], value: number) => ({
  id,
  kind,
  basis,
  value,
});

// Expected values come from the server's own order_math (src/void_pos/totals.py).
const CASES: [string, MathLine[], MathAdjustment[], ReturnType<typeof orderMath>][] = [
  [
    "an item with a modifier per unit",
    [line("a", null, 2, 1850, 0.1), line("b", "a", 1, 150, 0.1)],
    [],
    { subtotal_cents: 4000, adjustments: {}, total_cents: 4000, gst_cents: 364 },
  ],
  [
    "mixed tax rates, rounded once per rate",
    [line("a", null, 1, 1999, 0.1), line("b", null, 3, 333, 0), line("c", null, 1, 1015, 0.15)],
    [],
    { subtotal_cents: 4013, adjustments: {}, total_cents: 4013, gst_cents: 314 },
  ],
  [
    "a percentage discount carries its GST off",
    [line("a", null, 2, 1850, 0.1), line("b", "a", 1, 150, 0.1)],
    [adj("d1", "discount", "percent", 1000)],
    { subtotal_cents: 4000, adjustments: { d1: -400 }, total_cents: 3600, gst_cents: 327 },
  ],
  [
    "a surcharge goes on the discounted bill",
    [line("a", null, 1, 1999, 0.1), line("b", null, 3, 333, 0)],
    [adj("s1", "surcharge", "percent", 1500), adj("d1", "discount", "amount", 250)],
    { subtotal_cents: 2998, adjustments: { d1: -250, s1: 412 }, total_cents: 3160, gst_cents: 192 },
  ],
  [
    "discounts never take the order below zero",
    [line("a", null, 1, 500, 0.1)],
    [adj("d1", "discount", "amount", 400), adj("d2", "discount", "amount", 400)],
    { subtotal_cents: 500, adjustments: { d1: -400, d2: -100 }, total_cents: 0, gst_cents: 0 },
  ],
  [
    "a negative modifier",
    [line("a", null, 3, 1200, 0.1), line("b", "a", 2, -50, 0.1)],
    [adj("s1", "surcharge", "percent", 1250)],
    { subtotal_cents: 3300, adjustments: { s1: 413 }, total_cents: 3713, gst_cents: 338 },
  ],
  [
    "half cents round up",
    [line("a", null, 1, 55, 0.1)],
    [adj("d1", "discount", "percent", 5000)],
    { subtotal_cents: 55, adjustments: { d1: -28 }, total_cents: 27, gst_cents: 2 },
  ],
];

describe("order math matches the server", () => {
  it.each(CASES)("%s", (_name, lines, adjustments, expected) => {
    expect(orderMath(lines, adjustments)).toEqual(expected);
  });

  it("rounds half away from zero", () => {
    expect(roundHalfUp(5n, 2n)).toBe(3);
    expect(roundHalfUp(-5n, 2n)).toBe(-3);
    expect(roundHalfUp(4n, 3n)).toBe(1);
    expect(roundHalfUp(0n, 7n)).toBe(0);
  });

  it("reads a tax rate as the exact decimal it prints as", () => {
    expect(rateRatio(0.1)).toEqual([1n, 10n]);
    expect(rateRatio(0.15)).toEqual([15n, 100n]);
    expect(rateRatio(0)).toEqual([0n, 1n]);
    expect(rateRatio(1)).toEqual([1n, 1n]);
  });
});
