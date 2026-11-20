// How the till takes payment, for the hybrid model: cash, or a card taken on the venue's own
// terminal and recorded here with the terminal's receipt or approval number (method
// "external"). Integrated card payments (Stripe) come later through the same seam.

export type PayMethod = "cash" | "external" | "stripe";

const LABELS: Record<string, string> = {
  cash: "Cash",
  external: "Card on terminal",
  stripe: "Card (Stripe)",
};

/** A person-facing name for a payment provider as the API reports it. */
export function providerLabel(provider: string): string {
  return LABELS[provider] ?? provider;
}

export const MAX_TERMINAL_REF = 200;

/** The terminal reference as it will be sent, or an error to show the cashier. */
export function terminalRef(input: string): { ref: string } | { error: string } {
  const ref = input.trim();
  if (!ref) return { error: "Enter the receipt or approval number from the terminal." };
  if (ref.length > MAX_TERMINAL_REF) return { error: "That reference is too long." };
  return { ref };
}

/** Shares of `totalCents` split `ways` ways, in whole cents: the first shares take the
 *  odd cents, so $10.00 three ways is 3.34, 3.33, 3.33. */
export function evenSplit(totalCents: number, ways: number): number[] {
  const n = Math.max(1, Math.floor(ways));
  const base = Math.floor(totalCents / n);
  const extra = totalCents - base * n;
  return Array.from({ length: n }, (_, i) => base + (i < extra ? 1 : 0));
}

/** A dollar amount typed by the cashier, in cents, or an error to show. */
export function parseDollars(input: string): { cents: number } | { error: string } {
  const text = input.trim().replace(/^\$/, "").replace(/,/g, "");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return { error: "Enter an amount like 12.50." };
  const [whole, frac = ""] = text.split(".");
  return { cents: Number(whole) * 100 + Number(frac.padEnd(2, "0")) };
}

/** Cents as the plain number a cashier types, e.g. 1250 -> "12.50". */
export function centsToInput(cents: number): string {
  return (cents / 100).toFixed(2);
}
