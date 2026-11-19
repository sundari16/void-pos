"use client";

import { useState } from "react";
import { formatCents } from "@/lib/money";
import { centsToInput, parseDollars, providerLabel, terminalRef } from "@/lib/payments";
import { refundable } from "@/lib/placed";
import type { OrderResponse } from "@/lib/types";

export interface RefundChoice {
  paymentId: string;
  amountCents: number;
  reason: string;
  externalRef: string | null;
}

/** Give money back against one of a paid order's payments, the same way it was paid. */
export function RefundDialog({
  order,
  busy,
  onRefund,
  onClose,
}: {
  order: OrderResponse;
  busy: boolean;
  onRefund: (choice: RefundChoice) => void;
  onClose: () => void;
}) {
  const options = refundable(order);
  const [paymentId, setPaymentId] = useState(options[0]?.payment.payment_id ?? "");
  const picked = options.find((o) => o.payment.payment_id === paymentId);
  const [amount, setAmount] = useState(picked ? centsToInput(picked.left) : "");
  const [reason, setReason] = useState("");
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const isCard = picked?.payment.provider === "external";

  function choose(id: string) {
    setPaymentId(id);
    const next = options.find((o) => o.payment.payment_id === id);
    if (next) setAmount(centsToInput(next.left));
  }

  function submit() {
    if (!picked) return;
    const parsed = parseDollars(amount);
    if ("error" in parsed || parsed.cents <= 0) return setError("Enter an amount like 12.50.");
    if (parsed.cents > picked.left) return setError(`At most ${formatCents(picked.left)} can go back.`);
    if (!reason.trim()) return setError("Say why the money is going back.");
    let externalRef: string | null = null;
    if (isCard) {
      const checked = terminalRef(ref);
      if ("error" in checked) return setError("Do the refund on the terminal and type its reference.");
      externalRef = checked.ref;
    }
    setError(null);
    onRefund({ paymentId: picked.payment.payment_id, amountCents: parsed.cents, reason: reason.trim(), externalRef });
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <form
        role="dialog"
        aria-label="Refund"
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="w-full max-w-sm space-y-3 rounded-2xl bg-surface p-4 shadow-xl"
      >
        <h2 className="font-semibold">Refund order #{order.order_number}</h2>
        {options.length === 0 ? (
          <p className="text-sm text-muted">Everything taken has already been given back.</p>
        ) : (
          <>
            <fieldset className="space-y-1.5">
              <legend className="mb-1 text-sm text-muted">Which payment</legend>
              {options.map(({ payment, left }) => (
                <label key={payment.payment_id} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name="refund-payment"
                    checked={payment.payment_id === paymentId}
                    onChange={() => choose(payment.payment_id)}
                  />
                  {providerLabel(payment.provider)} {formatCents(payment.amount_cents + payment.tip_cents)}
                  {left < payment.amount_cents + payment.tip_cents && (
                    <span className="text-muted">({formatCents(left)} left)</span>
                  )}
                </label>
              ))}
            </fieldset>
            <label className="flex items-center rounded-lg border border-border bg-background px-3">
              <span className="text-muted">$</span>
              <input
                aria-label="Refund amount"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                inputMode="decimal"
                className="w-full bg-transparent px-1 py-2 font-semibold tabular-nums outline-none"
              />
            </label>
            <input
              aria-label="Refund reason"
              value={reason}
              onChange={(e) => setReason(e.target.value.slice(0, 200))}
              placeholder="Why (e.g. Cold food)"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
            {isCard && (
              <input
                aria-label="Terminal refund reference"
                value={ref}
                onChange={(e) => setRef(e.target.value)}
                placeholder="Terminal refund reference"
                autoComplete="off"
                className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm"
              />
            )}
            {isCard && (
              <p className="text-xs text-muted">
                Do the refund on the card terminal first, then type its reference here.
              </p>
            )}
          </>
        )}
        {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="submit"
            disabled={busy || options.length === 0}
            className="rounded-xl bg-red-600 px-4 py-2.5 font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Refunding…" : "Refund"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-border px-4 py-2.5 hover:bg-background"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
