// The pure parts of the till's approval, discount, refund, tip, card and open-orders flows,
// taken from the web till (web/src/app/page.tsx and its components) so the two tills behave
// the same. The screens only render these and collect input.

import { ApiError } from "./lib/api";
import { formatCents } from "./lib/money";
import { parseDollars, terminalRef } from "./lib/payments";
import { tipFor } from "./lib/placed";
import type { OrderResponse, OrderSummary, PaymentOut } from "./lib/types";
import { summaryOf } from "./offline/projection";

export const VOID_REASONS = ["Customer cancelled", "Rang up by mistake", "Kitchen can't make it"];
export const REMOVE_REASONS = ["Customer changed mind", "Rang up by mistake", "Out of stock"];
export const DISCOUNT_PERCENTS = [500, 1000, 1500, 2000];
export const SPLITS = [2, 3, 4];
export const TIPS = [1000, 1500];

const NEEDS_PIN = new Set(["manager_pin_required", "wrong_pin"]);

export function needsPin(err: unknown): err is ApiError {
  return err instanceof ApiError && NEEDS_PIN.has(err.code);
}

/** A manager PIN is 4 to 8 digits. */
export function validPin(pin: string): boolean {
  return /^[0-9]{4,8}$/.test(pin);
}

export function cleanPin(input: string): string {
  return input.replace(/\D/g, "").slice(0, 8);
}

/** Try an action; if it needs a manager's approval, ask for the PIN and try again (saying
 *  so when a PIN was wrong). Resolves to null if the manager cancels. */
export async function withApproval<T>(
  ask: (error?: string) => Promise<string | null>,
  attempt: (pin?: string) => Promise<T>,
): Promise<T | null> {
  let pin: string | undefined;
  for (;;) {
    try {
      return await attempt(pin);
    } catch (err) {
      if (!needsPin(err)) throw err;
      const next = await ask(err.code === "wrong_pin" && pin ? "That PIN isn't a manager's." : undefined);
      if (next === null) return null;
      pin = next;
    }
  }
}

export interface NewAdjustment {
  kind: "discount" | "surcharge";
  basis: "percent" | "amount";
  value: number;
  label: string | null;
}

/** A discount from the panel: a chosen percentage, or a typed dollar amount. */
export function discountFrom(
  percent: number | null,
  amountText: string,
  label: string,
): NewAdjustment | { error: string } {
  const why = label.trim().slice(0, 80) || null;
  if (percent !== null) return { kind: "discount", basis: "percent", value: percent, label: why };
  const parsed = parseDollars(amountText);
  if ("error" in parsed || parsed.cents <= 0) return { error: "Enter a discount like 5.00, or pick a percentage." };
  return { kind: "discount", basis: "amount", value: parsed.cents, label: why };
}

export function holidaySurcharge(basisPoints: number): NewAdjustment {
  return { kind: "surcharge", basis: "percent", value: basisPoints, label: "Public holiday" };
}

export interface RefundChoice {
  paymentId: string;
  amountCents: number;
  reason: string;
  externalRef: string | null;
}

/** A refund against one payment: no more than is left on it, with a reason, and for a
 *  card-on-terminal payment the terminal's refund reference. */
export function refundFrom(
  option: { payment: PaymentOut; left: number },
  amountText: string,
  reason: string,
  ref: string,
): RefundChoice | { error: string } {
  const parsed = parseDollars(amountText);
  if ("error" in parsed || parsed.cents <= 0) return { error: "Enter an amount like 12.50." };
  if (parsed.cents > option.left) return { error: `At most ${formatCents(option.left)} can go back.` };
  if (!reason.trim()) return { error: "Say why the money is going back." };
  let externalRef: string | null = null;
  if (option.payment.provider === "external") {
    const checked = terminalRef(ref);
    if ("error" in checked) return { error: "Do the refund on the terminal and type its reference." };
    externalRef = checked.ref;
  }
  return {
    paymentId: option.payment.payment_id,
    amountCents: parsed.cents,
    reason: reason.trim().slice(0, 200),
    externalRef,
  };
}

/** The amount being paid now: at most what's owing, and above zero while anything is. */
export function payAmount(text: string, balanceCents: number): { cents: number } | { error: string } {
  const parsed = parseDollars(text);
  if ("error" in parsed) return parsed;
  if (parsed.cents > balanceCents) return { error: `That's more than the ${formatCents(balanceCents)} owing.` };
  if (parsed.cents === 0 && balanceCents > 0) return { error: "Enter an amount above zero." };
  return parsed;
}

/** A tip: a share of the amount (basis points), or a typed amount when `basisPoints` is null. */
export function tipAmount(
  amountCents: number,
  basisPoints: number | null,
  text: string,
): { cents: number } | { error: string } {
  if (basisPoints !== null) return { cents: tipFor(amountCents, basisPoints) };
  const parsed = parseDollars(text || "0");
  return "error" in parsed ? { error: "Enter a tip like 5.00." } : parsed;
}

/** What to tell the cashier after Stripe's payment sheet closes; null when the customer
 *  simply cancelled. */
export function cardSheetError(error: { code?: string; message?: string } | undefined): string | null {
  if (!error || error.code === "Canceled") return null;
  return error.message || "The card was declined.";
}

/** The open-orders list: the server's, with this till's own copy of any order it has
 *  changes queued for (newer than the server's, or not there yet). */
export function mergeOpenOrders(
  server: OrderSummary[],
  local: OrderResponse[],
  queued: Set<string>,
): OrderSummary[] {
  const mine = new Map(local.filter((o) => queued.has(o.id)).map((o) => [o.id, summaryOf(o)]));
  const merged = new Map<string, OrderSummary>();
  for (const o of server) merged.set(o.id, mine.get(o.id) ?? o);
  for (const [id, o] of mine) if (!merged.has(id) && (o.state === "open" || o.state === "sent")) merged.set(id, o);
  return [...merged.values()]
    .filter((o) => o.state === "open" || o.state === "sent")
    .sort((a, b) => (a.id < b.id ? 1 : -1));
}
