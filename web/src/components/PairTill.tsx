"use client";

import { useState, type FormEvent } from "react";
import { api } from "@/lib/api";
import { friendlyError } from "@/lib/commands";
import { apiBase } from "@/lib/config";
import { tillFromPairing } from "@/lib/till";
import { useTill } from "./TillProvider";

/** Shown until this browser is paired as a till. */
export function PairTill() {
  const { pairWith, notice } = useTill();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      pairWith(tillFromPairing(await api.pair(code.trim())));
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mx-auto mt-8 max-w-sm space-y-4 rounded-2xl border border-border bg-surface p-6"
    >
      <div>
        <h1 className="text-lg font-semibold">Pair this till</h1>
        <p className="mt-1 text-sm text-muted">
          Enter the pairing code from the venue admin. Each code works once.
        </p>
      </div>

      {notice && (
        <p className="rounded-lg border border-amber-300 bg-amber-50 p-2 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          {notice}
        </p>
      )}

      <label htmlFor="pairing-code" className="block text-sm font-medium">
        Pairing code
      </label>
      <input
        id="pairing-code"
        value={code}
        onChange={(e) => setCode(e.target.value.toUpperCase())}
        placeholder="K7QM-4XPA"
        autoComplete="one-time-code"
        autoCapitalize="characters"
        spellCheck={false}
        className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-lg tracking-widest"
      />

      {error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy || !code.trim()}
        className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
      >
        {busy ? "Pairing…" : "Pair till"}
      </button>

      <p className="text-xs text-muted">
        Server <span className="font-mono">{apiBase()}</span>
      </p>
    </form>
  );
}
