// Sending a till command so that retrying is always safe.
//
// The request body (with its event_id, seq and order/payment ids) is built once and kept
// in `pending` until the server answers. A dropped connection or a 5xx keeps it, so the
// next tap resends the very same ids and the server replays the first result instead of
// placing, printing or charging twice. A 4xx is final, so the body is dropped. A
// seq_conflict means this till's counter fell behind the server: take a fresh seq after
// the server's last one and resend once, keeping the event_id; `repair` lets a command
// fix its own body the same way (a reused order number, say).

import { ApiError } from "./api";
import type { CommandIn } from "./types";

export interface Pending<B> {
  current: B | null;
}

export interface RunOptions<B extends CommandIn, R> {
  pending: Pending<B>;
  build: () => B;
  send: (body: B) => Promise<R>;
  /** Resync the till's counter past `lastSeq` and return a fresh seq. */
  seqAfter: (lastSeq: number) => number;
  onUnauthorized: () => void;
  /** Fix the body after a refusal it can recover from; null means it can't. */
  repair?: (err: ApiError, body: B) => B | null;
}

function repaired<B extends CommandIn, R>(err: unknown, body: B, opts: RunOptions<B, R>): B | null {
  if (!(err instanceof ApiError)) return null;
  const lastSeq = err.body.last_seq;
  if (err.code === "seq_conflict" && typeof lastSeq === "number") {
    return { ...body, seq: opts.seqAfter(lastSeq) };
  }
  return opts.repair?.(err, body) ?? null;
}

export function isRetryable(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 0 || err.status >= 500);
}

export async function runCommand<B extends CommandIn, R>(opts: RunOptions<B, R>): Promise<R> {
  let body: B = opts.pending.current ?? opts.build();
  opts.pending.current = body;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const result = await opts.send(body);
      opts.pending.current = null;
      return result;
    } catch (err) {
      const fixed = attempt === 0 ? repaired(err, body, opts) : null;
      if (fixed) {
        body = fixed;
        opts.pending.current = body;
        continue;
      }
      if (err instanceof ApiError && err.status === 401) opts.onUnauthorized();
      if (!isRetryable(err)) opts.pending.current = null;
      throw err;
    }
  }
}

const MESSAGES: Record<string, string> = {
  network_error:
    "Can't reach the Void POS server. Check the till's Wi-Fi, then tap again. Retrying is safe.",
  printer_unavailable:
    "The kitchen printer isn't answering. Check it's on and connected, then send again.",
  order_closed: "This order is already closed.",
  order_voided: "This order was voided, so it can't go to the kitchen.",
  invalid_token: "This till's pairing was revoked or replaced. Pair it again.",
  invalid_pairing_code: "That pairing code isn't valid. Check it, or ask for a new one.",
  pairing_code_expired: "That pairing code has expired. Ask the venue admin for a new one.",
};

export function friendlyError(err: unknown): string {
  if (err instanceof ApiError) return MESSAGES[err.code] ?? err.detail;
  return err instanceof Error ? err.message : String(err);
}
