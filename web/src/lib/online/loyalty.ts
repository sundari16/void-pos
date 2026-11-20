// Loyalty redeem arithmetic, mirroring the backend (src/void_pos/loyalty/service.py), so the
// storefront shows the same "you can take $X off" the server will apply. Money is cents.

/** The most points that can go toward a bill: never more than the balance, never more than
 *  the bill is worth in points. */
export function maxRedeemablePoints(
  balance: number,
  owingCents: number,
  redeemCentsPerPoint: number,
): number {
  if (balance <= 0 || owingCents <= 0 || redeemCentsPerPoint <= 0) return 0;
  return Math.max(0, Math.min(balance, Math.floor(owingCents / redeemCentsPerPoint)));
}

/** What `points` are worth off the bill, in cents. */
export function redemptionValueCents(points: number, redeemCentsPerPoint: number): number {
  return Math.max(0, points) * redeemCentsPerPoint;
}
