// An order's money, worked out on the till exactly as the server does it
// (src/void_pos/totals.py), so an order rung up offline shows the total, GST and balance the
// server will agree with once it syncs. Integer cents throughout; the server's Decimal
// arithmetic (rounded half up) is done here with exact integer ratios (BigInt).

export interface MathLine {
  id: string;
  parent_line_id: string | null;
  quantity: number;
  unit_price_cents: number;
  tax_rate: number;
}

export interface MathAdjustment {
  id: string;
  kind: "discount" | "surcharge";
  basis: "percent" | "amount";
  /** Basis points, or cents. */
  value: number;
}

export interface OrderMath {
  subtotal_cents: number;
  /** Adjustment id -> signed cents (discounts negative). */
  adjustments: Record<string, number>;
  total_cents: number;
  gst_cents: number;
}

/** n / d rounded half away from zero, as Python's ROUND_HALF_UP does. d > 0. */
export function roundHalfUp(n: bigint, d: bigint): number {
  if (n < 0n) return -roundHalfUp(-n, d);
  return Number((2n * n + d) / (2n * d));
}

/** A tax rate as an exact ratio: 0.1 -> 1/10, matching Python's Decimal(str(rate)). */
export function rateRatio(rate: number): [bigint, bigint] {
  const text = String(rate);
  const m = /^(\d+)(?:\.(\d+))?$/.exec(text);
  if (!m) {
    const scaled = Math.round(rate * 1_000_000);
    return [BigInt(scaled), 1_000_000n];
  }
  const frac = m[2] ?? "";
  return [BigInt(m[1] + frac), 10n ** BigInt(frac.length)];
}

/** Each line's quantity for the whole order: a modifier's is its own times its item's. */
export function extendedQuantities(lines: MathLine[]): Map<string, number> {
  const itemQty = new Map<string, number>();
  for (const line of lines) if (line.parent_line_id === null) itemQty.set(line.id, line.quantity);
  return new Map(
    lines.map((line) => [
      line.id,
      line.parent_line_id === null
        ? line.quantity
        : line.quantity * (itemQty.get(line.parent_line_id) ?? 1),
    ]),
  );
}

export function totalCents(lines: MathLine[]): number {
  const qty = extendedQuantities(lines);
  return lines.reduce((sum, line) => sum + line.unit_price_cents * (qty.get(line.id) ?? 0), 0);
}

function amountsByRate(lines: MathLine[]): Map<string, { rate: [bigint, bigint]; cents: number }> {
  const qty = extendedQuantities(lines);
  const byRate = new Map<string, { rate: [bigint, bigint]; cents: number }>();
  for (const line of lines) {
    const ratio = rateRatio(line.tax_rate);
    const key = `${ratio[0]}/${ratio[1]}`;
    const entry = byRate.get(key) ?? { rate: ratio, cents: 0 };
    entry.cents += line.unit_price_cents * (qty.get(line.id) ?? 0);
    byRate.set(key, entry);
  }
  return byRate;
}

/** The GST included in the prices: rate / (1 + rate) of each rate's amount, rounded once
 *  per rate. Scaled by `running / subtotal` when adjustments change the total. */
function gstCents(lines: MathLine[], running?: number, subtotal?: number): number {
  let gst = 0;
  for (const { rate, cents } of amountsByRate(lines).values()) {
    const [num, den] = rate;
    if (num === 0n) continue;
    let n = BigInt(cents) * num;
    let d = den + num;
    if (running !== undefined && subtotal !== undefined) {
      n *= BigInt(running);
      d *= BigInt(subtotal);
    }
    gst += roundHalfUp(n, d);
  }
  return gst;
}

function share(cents: number, basisPoints: number): number {
  return roundHalfUp(BigInt(cents) * BigInt(basisPoints), 10_000n);
}

/** Discounts come off the items' subtotal (never below zero), then surcharges go on what's
 *  left; GST scales with the final total. */
export function orderMath(lines: MathLine[], adjustments: MathAdjustment[] = []): OrderMath {
  const subtotal = totalCents(lines);
  const amounts: Record<string, number> = {};
  let running = subtotal;
  for (const adj of adjustments.filter((a) => a.kind === "discount")) {
    let off = adj.basis === "percent" ? share(subtotal, adj.value) : adj.value;
    off = Math.min(off, Math.max(running, 0));
    amounts[adj.id] = -off;
    running -= off;
  }
  const base = running;
  for (const adj of adjustments.filter((a) => a.kind === "surcharge")) {
    const on = adj.basis === "percent" ? share(base, adj.value) : adj.value;
    amounts[adj.id] = on;
    running += on;
  }
  if (subtotal <= 0 || running === subtotal) {
    return { subtotal_cents: subtotal, adjustments: amounts, total_cents: running, gst_cents: gstCents(lines) };
  }
  return {
    subtotal_cents: subtotal,
    adjustments: amounts,
    total_cents: running,
    gst_cents: gstCents(lines, running, subtotal),
  };
}
