"use client";

import { useState } from "react";

const REASONS = ["Customer changed mind", "Rang up by mistake", "Out of stock"];

/** Take an item off a placed order, with a reason. To change a quantity, take it off and
 *  add it again. */
export function RemoveItemDialog({
  name,
  sent,
  busy,
  onRemove,
  onClose,
}: {
  name: string;
  /** The kitchen has it: a CANCEL ticket prints, and a manager approves. */
  sent: boolean;
  busy: boolean;
  onRemove: (reason: string) => void;
  onClose: () => void;
}) {
  const [choice, setChoice] = useState<string | null>(null);
  const [other, setOther] = useState("");
  const reason = (choice ?? other).trim();

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <form
        role="dialog"
        aria-label={`Take off ${name}`}
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (reason) onRemove(reason);
        }}
        className="w-full max-w-sm space-y-3 rounded-2xl bg-surface p-4 shadow-xl"
      >
        <h2 className="font-semibold">Take off {name}?</h2>
        {sent && (
          <p className="text-sm text-muted">
            The kitchen has this: a CANCEL ticket prints, and a manager approves.
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {REASONS.map((r) => (
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
            setOther(e.target.value.slice(0, 200));
          }}
          placeholder="Other reason"
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
        />
        <div className="grid grid-cols-2 gap-2">
          <button
            type="submit"
            disabled={busy || !reason}
            className="rounded-xl bg-red-600 px-4 py-2.5 font-semibold text-white disabled:opacity-40"
          >
            {busy ? "Taking off…" : "Take it off"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-border px-4 py-2.5 hover:bg-background"
          >
            Keep it
          </button>
        </div>
      </form>
    </div>
  );
}
