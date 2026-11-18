"use client";

import { useEffect, useState } from "react";
import { friendlyError } from "@/lib/commands";
import { formatCents } from "@/lib/money";
import type { OrderSummary } from "@/lib/types";

/** The location's open and sent orders, newest first; tap one to carry on with it. */
export function OpenOrders({
  load,
  onPick,
  onClose,
}: {
  load: () => Promise<OrderSummary[]>;
  onPick: (order: OrderSummary) => void;
  onClose: () => void;
}) {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    load()
      .then((list) => active && setOrders(list))
      .catch((err) => active && setError(friendlyError(err)));
    return () => {
      active = false;
    };
  }, [load]);

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-end bg-black/40 sm:place-items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label="Open orders"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[85dvh] w-full max-w-lg flex-col overflow-hidden rounded-t-2xl bg-surface shadow-xl sm:rounded-2xl"
      >
        <div className="flex items-center justify-between border-b border-border p-4">
          <h2 className="text-lg font-semibold">Open orders</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-1 text-xl leading-none text-muted hover:bg-background"
          >
            ×
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {error && <p className="p-3 text-sm text-red-700 dark:text-red-300">{error}</p>}
          {!orders && !error && <p className="p-3 text-sm text-muted">Loading…</p>}
          {orders?.length === 0 && (
            <p className="p-3 text-sm text-muted">No open orders. Everything is paid or voided.</p>
          )}
          <ul>
            {orders?.map((o) => (
              <li key={o.id}>
                <button
                  onClick={() => onPick(o)}
                  className="flex w-full items-center justify-between gap-3 rounded-xl p-3 text-left hover:bg-background"
                >
                  <span>
                    <span className="font-semibold">
                      {o.table_label ? `Table ${o.table_label}` : o.fulfillment.replace("_", "-")}
                    </span>{" "}
                    <span className="text-muted">#{o.order_number}</span>
                    <span className="block text-xs text-muted">
                      {new Date(o.created_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                      {" · "}
                      {o.state}
                      {o.unsent_lines > 0 && " · new items not sent"}
                    </span>
                  </span>
                  <span className="text-right tabular-nums">
                    <span className="block font-semibold">{formatCents(o.balance_cents)}</span>
                    {o.balance_cents !== o.total_cents && (
                      <span className="block text-xs text-muted">of {formatCents(o.total_cents)}</span>
                    )}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  );
}
