"use client";

import { useState } from "react";

/** Ask a manager for their PIN. The PIN is only held long enough to send it once. */
export function PinPrompt({
  title,
  error,
  onSubmit,
  onCancel,
}: {
  title: string;
  error?: string;
  onSubmit: (pin: string) => void;
  onCancel: () => void;
}) {
  const [pin, setPin] = useState("");
  const valid = /^[0-9]{4,8}$/.test(pin);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onClick={onCancel}>
      <form
        role="dialog"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onSubmit(pin);
        }}
        className="w-full max-w-xs space-y-3 rounded-2xl bg-surface p-4 shadow-xl"
      >
        <h2 className="font-semibold">{title}</h2>
        <label htmlFor="manager-pin" className="block text-sm text-muted">
          Manager PIN
        </label>
        <input
          id="manager-pin"
          value={pin}
          onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, 8))}
          type="password"
          inputMode="numeric"
          autoComplete="off"
          autoFocus
          className="w-full rounded-lg border border-border bg-background px-3 py-2 text-center text-2xl tracking-[0.5em]"
        />
        {error && <p className="text-sm text-red-700 dark:text-red-300">{error}</p>}
        <div className="grid grid-cols-2 gap-2">
          <button
            type="submit"
            disabled={!valid}
            className="rounded-xl bg-accent px-4 py-2.5 font-semibold text-white disabled:opacity-40"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="rounded-xl border border-border px-4 py-2.5 font-medium hover:bg-background"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
