import { describe, expect, it } from "vitest";
import { maxRedeemablePoints, redemptionValueCents } from "./loyalty";

describe("loyalty redeem maths", () => {
  it("caps redeemable points by the balance and by the bill", () => {
    expect(maxRedeemablePoints(50, 10_000, 1)).toBe(50); // balance is the limit
    expect(maxRedeemablePoints(10_000, 300, 1)).toBe(300); // the bill is the limit
    expect(maxRedeemablePoints(10_000, 300, 5)).toBe(60); // 5c/point -> $3 takes 60
  });

  it("is zero with no balance, no bill, or no redeem value", () => {
    expect(maxRedeemablePoints(0, 300, 1)).toBe(0);
    expect(maxRedeemablePoints(50, 0, 1)).toBe(0);
    expect(maxRedeemablePoints(50, 300, 0)).toBe(0);
  });

  it("values points in cents", () => {
    expect(redemptionValueCents(18, 1)).toBe(18);
    expect(redemptionValueCents(18, 5)).toBe(90);
    expect(redemptionValueCents(-3, 1)).toBe(0);
  });
});
