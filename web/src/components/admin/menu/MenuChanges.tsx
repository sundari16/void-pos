"use client";

import { useState } from "react";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import { describeChange, undoneBy } from "@/lib/adminMenu";
import { formatDateTime } from "@/lib/adminSession";
import type { MenuChange } from "@/lib/adminTypes";
import { Badge, smallButton } from "../ui";

/** The menu's change history, newest first, each with an Undo. The server only undoes the
 *  latest change to an item, group or modifier, and says why when it won't. */
export function MenuChanges({
  api,
  changes,
  names,
  timezone,
  onChanged,
}: {
  api: AdminApi;
  changes: MenuChange[];
  names: Map<string, string>;
  timezone: string;
  onChanged: () => Promise<void>;
}) {
  const undone = undoneBy(changes);
  if (changes.length === 0) {
    return (
      <p className="rounded-2xl border border-border bg-surface p-6 text-muted">
        No menu changes recorded yet.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border rounded-2xl border border-border bg-surface">
      {changes.map((change) => (
        <ChangeRow
          key={change.id}
          api={api}
          change={change}
          text={describeChange(change, names)}
          undoneByNumber={undone.get(change.id)}
          timezone={timezone}
          onChanged={onChanged}
        />
      ))}
    </ul>
  );
}

function ChangeRow({
  api,
  change,
  text,
  undoneByNumber,
  timezone,
  onChanged,
}: {
  api: AdminApi;
  change: MenuChange;
  text: string;
  undoneByNumber: number | undefined;
  timezone: string;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function undo() {
    setBusy(true);
    setError(null);
    try {
      await api.revertChange(change.id);
      await onChanged();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="w-12 shrink-0 text-sm tabular-nums text-muted">#{change.number}</span>
        <div className="min-w-[12rem] flex-1">
          <p className={undoneByNumber ? "text-muted line-through" : ""}>{text}</p>
          <p className="text-xs text-muted">
            {formatDateTime(change.changed_at, timezone)} · {change.actor}
          </p>
        </div>
        {undoneByNumber && <Badge>Undone by #{undoneByNumber}</Badge>}
        <button onClick={undo} disabled={busy} className={smallButton}>
          {busy ? "Undoing…" : "Undo"}
        </button>
      </div>
      {error && (
        <p role="alert" className="mt-2 pl-15 text-sm text-red-700 dark:text-red-300">
          {error}
        </p>
      )}
    </li>
  );
}
