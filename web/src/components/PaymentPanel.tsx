"use client";

import { useState } from "react";
import { formatCents } from "@/lib/money";
import {
  centsToInput,
  evenSplit,
  parseDollars,
  terminalRef,
  type PayMethod,
} from "@/lib/payments";
import { percentText, tipFor } from "@/lib/placed";

const SPLITS = [2, 3, 4];
const TIPS = [1000, 1500];

interface Split {
  shares: number[];
  index: number;
}

/** Take the balance, or part of it: a typed amount, or an even share of a split bill.
 *  After each payment the amount moves on to what is left (or the next share). */
export function PaymentPanel({
  balanceCents,
  busy,
  retryMethod,
  onPay,
}: {
  balanceCents: number;
  busy: boolean;
  /** An unconfirmed earlier payment: the only way on is to resend it as it was. */
  retryMethod: PayMethod | null;
  onPay: (method: PayMethod, amountCents: number, externalRef?: string, tipCents?: number) => void;
}) {
  const [split, setSplit] = useState<Split | null>(null);
  const [amount, setAmount] = useState(centsToInput(balanceCents));
  const [lastBalance, setLastBalance] = useState(balanceCents);
  const [cardOpen, setCardOpen] = useState(false);
  const [cardRef, setCardRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  // A tip: a share of the amount being paid, or a typed amount; 0 = none.
  const [tipBp, setTipBp] = useState<number | null>(0);
  const [tipText, setTipText] = useState("");

  // A payment went through: move on to the next share, or to whatever is left.
  if (balanceCents !== lastBalance) {
    setLastBalance(balanceCents);
    const index = split ? split.index + 1 : 0;
    const nextSplit = split && index < split.shares.length ? { ...split, index } : null;
    setSplit(nextSplit);
    setAmount(
      centsToInput(nextSplit ? Math.min(nextSplit.shares[index], balanceCents) : balanceCents),
    );
    setCardOpen(false);
    setCardRef("");
    setError(null);
    setTipBp(0);
    setTipText("");
  }

  function chooseSplit(ways: number | null) {
    setError(null);
    if (ways === null) {
      setSplit(null);
      setAmount(centsToInput(balanceCents));
      return;
    }
    const shares = evenSplit(balanceCents, ways);
    setSplit({ shares, index: 0 });
    setAmount(centsToInput(shares[0]));
  }

  function checkedAmount(): number | null {
    const parsed = parseDollars(amount);
    if ("error" in parsed) {
      setError(parsed.error);
      return null;
    }
    if (parsed.cents > balanceCents) {
      setError(`That's more than the ${formatCents(balanceCents)} owing.`);
      return null;
    }
    if (parsed.cents === 0 && balanceCents > 0) {
      setError("Enter an amount above zero.");
      return null;
    }
    setError(null);
    return parsed.cents;
  }

  function tipCents(amountCents: number): number | null {
    if (tipBp !== null) return tipFor(amountCents, tipBp);
    const parsed = parseDollars(tipText || "0");
    if ("error" in parsed) {
      setError("Enter a tip like 5.00.");
      return null;
    }
    return parsed.cents;
  }

  function payCash() {
    const cents = checkedAmount();
    const tip = cents === null ? null : tipCents(cents);
    if (cents !== null && tip !== null) onPay("cash", cents, undefined, tip);
  }

  function payCard() {
    const cents = checkedAmount();
    if (cents === null) return;
    const checked = terminalRef(cardRef);
    if ("error" in checked) {
      setError(checked.error);
      return;
    }
    const tip = tipCents(cents);
    if (tip === null) return;
    onPay("external", cents, checked.ref, tip);
  }

  if (retryMethod) {
    return (
      <button
        onClick={() => onPay(retryMethod, 0)}
        disabled={busy}
        className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
      >
        {busy ? "Paying…" : `Retry ${retryMethod === "external" ? "card" : "cash"} payment`}
      </button>
    );
  }

  return (
    <div className="space-y-2 rounded-xl border border-border p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">
          {split ? `Share ${split.index + 1} of ${split.shares.length}` : "Amount"}
        </span>
        <span className="text-sm text-muted">Owing {formatCents(balanceCents)}</span>
      </div>
      <div className="flex gap-2">
        <label className="flex flex-1 items-center rounded-lg border border-border bg-background px-3">
          <span className="text-muted">$</span>
          <input
            aria-label="Amount to pay"
            value={amount}
            onChange={(e) => {
              setAmount(e.target.value);
              setError(null);
            }}
            inputMode="decimal"
            autoComplete="off"
            className="w-full bg-transparent px-1 py-2 font-semibold tabular-nums outline-none"
          />
        </label>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Split the bill">
        <button
          type="button"
          onClick={() => chooseSplit(null)}
          aria-pressed={split === null}
          className={chip(split === null)}
        >
          Whole
        </button>
        {SPLITS.map((ways) => (
          <button
            key={ways}
            type="button"
            onClick={() => chooseSplit(ways)}
            aria-pressed={split?.shares.length === ways}
            disabled={balanceCents < ways}
            className={chip(split?.shares.length === ways)}
          >
            Split {ways}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Tip">
        <span className="text-sm text-muted">Tip</span>
        <button type="button" onClick={() => setTipBp(0)} aria-pressed={tipBp === 0} className={chip(tipBp === 0)}>
          None
        </button>
        {TIPS.map((bp) => (
          <button
            key={bp}
            type="button"
            onClick={() => setTipBp(bp)}
            aria-pressed={tipBp === bp}
            className={chip(tipBp === bp)}
          >
            {percentText(bp)}
          </button>
        ))}
        <button type="button" onClick={() => setTipBp(null)} aria-pressed={tipBp === null} className={chip(tipBp === null)}>
          $
        </button>
        {tipBp === null && (
          <input
            aria-label="Tip amount"
            value={tipText}
            onChange={(e) => setTipText(e.target.value)}
            inputMode="decimal"
            placeholder="0.00"
            className="w-20 rounded-lg border border-border bg-background px-2 py-1 text-sm tabular-nums"
          />
        )}
      </div>

      {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}

      {cardOpen ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            payCard();
          }}
          className="space-y-2"
        >
          <label htmlFor="card-ref" className="block text-sm font-medium">
            Terminal receipt or approval number
          </label>
          <input
            id="card-ref"
            value={cardRef}
            onChange={(e) => setCardRef(e.target.value)}
            autoFocus
            autoComplete="off"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono"
          />
          <div className="grid grid-cols-2 gap-2">
            <button
              type="submit"
              disabled={busy || !cardRef.trim()}
              className="rounded-xl bg-accent px-4 py-2.5 font-semibold text-white disabled:opacity-40"
            >
              {busy ? "Recording…" : "Record card payment"}
            </button>
            <button
              type="button"
              onClick={() => {
                setCardOpen(false);
                setCardRef("");
              }}
              disabled={busy}
              className="rounded-xl border border-border px-4 py-2.5 font-medium hover:bg-background"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={payCash}
            disabled={busy}
            className="rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Paying…" : "Pay cash"}
          </button>
          <button
            onClick={() => {
              if (checkedAmount() !== null) setCardOpen(true);
            }}
            disabled={busy}
            className="rounded-xl border border-accent px-4 py-3 font-semibold text-accent disabled:opacity-40"
          >
            Card on terminal
          </button>
        </div>
      )}
    </div>
  );
}

function chip(on: boolean): string {
  return `rounded-full border px-3 py-1 text-sm disabled:opacity-40 ${
    on ? "border-accent bg-accent text-white" : "border-border hover:border-accent"
  }`;
}
