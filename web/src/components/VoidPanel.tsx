"use client";

import { useState } from "react";

export const VOID_REASONS = ["Customer cancelled", "Rang up by mistake", "Kitchen can't make it"];

/** "Void order", then a reason (a common one or typed), then confirm. */
export function VoidPanel({
  busy,
  retrying,
  onVoid,
}: {
  busy: boolean;
  /** An earlier void wasn't confirmed: the only way on is to resend it as it was. */
  retrying: boolean;
  onVoid: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const reason = (choice ?? other).trim();

  if (retrying) {
    return (
      <button
        onClick={() => onVoid("")}
        disabled={busy}
        className="w-full rounded-xl border border-red-400 px-4 py-2.5 font-semibold text-red-700 disabled:opacity-40 dark:text-red-300"
      >
        {busy ? "Voiding…" : "Retry void"}
      </button>
    );
  }
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-full rounded-xl px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 dark:text-red-300 dark:hover:bg-red-950/40"
      >
        Void order
      </button>
    );
  }
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (reason) onVoid(reason);
      }}
      className="space-y-2 rounded-xl border border-red-300 p-3 dark:border-red-900"
    >
      <p className="text-sm font-medium">Why is this order being voided?</p>
      <div className="flex flex-wrap gap-2">
        {VOID_REASONS.map((r) => (
          <button
            key={r}
            type="button"
            aria-pressed={choice === r}
            onClick={() => setChoice(choice === r ? null : r)}
            className={`rounded-full border px-3 py-1.5 text-sm ${
              choice === r ? "border-red-600 bg-red-600 text-white" : "border-border hover:border-red-400"
            }`}
          >
            {r}
          </button>
        ))}
      </div>
      <input
        aria-label="Other reason"
        value={other}
        onChange={(e) => {
          setChoice(null);
          setOther(e.target.value);
        }}
        maxLength={200}
        placeholder="Other reason"
        autoComplete="off"
        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
      />
      <div className="grid grid-cols-2 gap-2">
        <button
          type="submit"
          disabled={busy || !reason}
          className="rounded-xl bg-red-600 px-4 py-2.5 font-semibold text-white disabled:opacity-40"
        >
          {busy ? "Voiding…" : "Void order"}
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          disabled={busy}
          className="rounded-xl border border-border px-4 py-2.5 font-medium hover:bg-background"
        >
          Keep order
        </button>
      </div>
    </form>
  );
}
