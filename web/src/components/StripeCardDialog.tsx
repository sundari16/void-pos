"use client";

import { useEffect, useRef, useState } from "react";
import { friendlyError } from "@/lib/commands";
import { formatCents } from "@/lib/money";
import { loadStripe, type StripeElements, type StripeJs } from "@/lib/stripe";
import type { StripeIntentOut } from "@/lib/types";

/** Take a card through Stripe on the till's screen. The customer types the card into
 *  Stripe's own form, which authorises it; the till then records the payment, and the
 *  server captures the money only as it records it. */
export function StripeCardDialog({
  amountCents,
  tipCents,
  start,
  onPaid,
  onClose,
}: {
  amountCents: number;
  tipCents: number;
  /** Ask the server for the payment intent (idempotent for this payment). */
  start: () => Promise<StripeIntentOut>;
  onPaid: (paymentIntentId: string) => void;
  onClose: () => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const stripeRef = useRef<{ stripe: StripeJs; elements: StripeElements } | null>(null);
  const [ready, setReady] = useState(false);
  const [paying, setPaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Start once per dialog: the page re-renders often (its clock), and remounting Stripe's
  // form would wipe a half-typed card.
  const startRef = useRef(start);

  useEffect(() => {
    let active = true;
    let element: { destroy(): void } | null = null;
    (async () => {
      try {
        const intent = await startRef.current();
        const stripe = await loadStripe(intent.publishable_key);
        if (!active || !mountRef.current) return;
        const elements = stripe.elements({
          clientSecret: intent.client_secret,
          appearance: { theme: "stripe" },
        });
        const payment = elements.create("payment", { layout: "tabs" });
        payment.mount(mountRef.current);
        element = payment;
        stripeRef.current = { stripe, elements };
        setReady(true);
      } catch (err) {
        if (active) setError(friendlyError(err));
      }
    })();
    return () => {
      active = false;
      element?.destroy();
    };
  }, []);

  async function pay() {
    const s = stripeRef.current;
    if (!s) return;
    setPaying(true);
    setError(null);
    try {
      const submitted = await s.elements.submit();
      if (submitted.error) {
        setError(submitted.error.message ?? "Check the card details.");
        return;
      }
      const result = await s.stripe.confirmPayment({ elements: s.elements, redirect: "if_required" });
      if (result.error) {
        setError(result.error.message ?? "The card was declined.");
      } else if (
        result.paymentIntent &&
        ["requires_capture", "succeeded"].includes(result.paymentIntent.status)
      ) {
        // Authorised: the till records it, and the server captures it then.
        onPaid(result.paymentIntent.id);
      } else {
        setError(`The payment is ${result.paymentIntent?.status ?? "not complete"}.`);
      }
    } finally {
      setPaying(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-label="Card payment"
        className="w-full max-w-md space-y-3 rounded-2xl bg-surface p-4 shadow-xl"
      >
        <div className="flex items-baseline justify-between">
          <h2 className="font-semibold">Card payment</h2>
          <span className="text-lg font-semibold tabular-nums">
            {formatCents(amountCents + tipCents)}
          </span>
        </div>
        {tipCents > 0 && (
          <p className="text-sm text-muted">
            {formatCents(amountCents)} plus a {formatCents(tipCents)} tip
          </p>
        )}
        <div ref={mountRef} className="min-h-24 rounded-lg bg-white p-2" />
        {!ready && !error && <p className="text-sm text-muted">Loading the card form…</p>}
        {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={pay}
            disabled={!ready || paying}
            className="rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
          >
            {paying ? "Paying…" : `Pay ${formatCents(amountCents + tipCents)}`}
          </button>
          <button
            onClick={onClose}
            disabled={paying}
            className="rounded-xl border border-border px-4 py-3 hover:bg-background disabled:opacity-40"
          >
            Cancel
          </button>
        </div>
        <p className="text-xs text-muted">Sandbox: test card 4242 4242 4242 4242, any future date, any CVC.</p>
      </div>
    </div>
  );
}
