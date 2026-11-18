"use client";

import { useState } from "react";
import { formatCents } from "@/lib/money";
import { adjustmentLabel, percentText } from "@/lib/placed";
import { parseDollars } from "@/lib/payments";
import type { AdjustmentOut } from "@/lib/types";

export interface NewAdjustment {
  kind: "discount" | "surcharge";
  basis: "percent" | "amount";
  value: number;
  label: string | null;
}

const PERCENTS = [500, 1000, 1500, 2000];

/** The order's discounts and surcharges, and buttons to add or take them off. A discount
 *  asks for a manager's PIN once the venue has a manager (the page handles that). */
export function AdjustPanel({
  adjustments,
  holidaySurchargeBp,
  busy,
  onAdd,
  onRemove,
}: {
  adjustments: AdjustmentOut[];
  holidaySurchargeBp: number | null | undefined;
  busy: boolean;
  onAdd: (adj: NewAdjustment) => void;
  onRemove: (adjustmentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [percent, setPercent] = useState<number | null>(1000);
  const [amount, setAmount] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const hasHoliday = adjustments.some((a) => a.kind === "surcharge" && a.label === "Public holiday");

  function submit() {
    setError(null);
    if (percent !== null) {
      onAdd({ kind: "discount", basis: "percent", value: percent, label: label.trim() || null });
    } else {
      const parsed = parseDollars(amount);
      if ("error" in parsed || parsed.cents <= 0) {
        setError("Enter a discount like 5.00, or pick a percentage.");
        return;
      }
      onAdd({ kind: "discount", basis: "amount", value: parsed.cents, label: label.trim() || null });
    }
    setOpen(false);
    setLabel("");
    setAmount("");
  }

  return (
    <div className="space-y-2">
      {adjustments.length > 0 && (
        <ul className="space-y-1 text-sm">
          {adjustments.map((a) => (
            <li key={a.id} className="flex items-center justify-between gap-2">
              <span>{adjustmentLabel(a)}</span>
              <span className="flex items-center gap-2 tabular-nums">
                {formatCents(a.amount_cents)}
                <button
                  onClick={() => onRemove(a.id)}
                  disabled={busy}
                  aria-label={`Take off ${adjustmentLabel(a)}`}
                  className="rounded px-1.5 text-muted hover:bg-background disabled:opacity-40"
                >
                  ×
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {open ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="space-y-2 rounded-xl border border-border p-3"
        >
          <p className="text-sm font-medium">Discount</p>
          <div className="flex flex-wrap gap-1.5">
            {PERCENTS.map((bp) => (
              <button
                key={bp}
                type="button"
                aria-pressed={percent === bp}
                onClick={() => setPercent(bp)}
                className={chip(percent === bp)}
              >
                {percentText(bp)}
              </button>
            ))}
            <button
              type="button"
              aria-pressed={percent === null}
              onClick={() => setPercent(null)}
              className={chip(percent === null)}
            >
              $ amount
            </button>
          </div>
          {percent === null && (
            <input
              aria-label="Discount amount"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              inputMode="decimal"
              placeholder="5.00"
              className="w-full rounded-lg border border-border bg-background px-3 py-2"
            />
          )}
          <input
            aria-label="Discount reason"
            value={label}
            onChange={(e) => setLabel(e.target.value.slice(0, 80))}
            placeholder="Reason (e.g. Staff meal)"
            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
          />
          {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
          <div className="grid grid-cols-2 gap-2">
            <button
              type="submit"
              disabled={busy}
              className="rounded-xl bg-accent px-3 py-2 font-semibold text-white disabled:opacity-40"
            >
              Apply discount
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded-xl border border-border px-3 py-2 hover:bg-background"
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setOpen(true)}
            disabled={busy}
            className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-background disabled:opacity-40"
          >
            Discount
          </button>
          {holidaySurchargeBp ? (
            <button
              onClick={() =>
                onAdd({
                  kind: "surcharge",
                  basis: "percent",
                  value: holidaySurchargeBp,
                  label: "Public holiday",
                })
              }
              disabled={busy || hasHoliday}
              className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-background disabled:opacity-40"
            >
              Public holiday surcharge {percentText(holidaySurchargeBp)}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

function chip(on: boolean): string {
  return `rounded-full border px-3 py-1 text-sm ${
    on ? "border-accent bg-accent text-white" : "border-border hover:border-accent"
  }`;
}
