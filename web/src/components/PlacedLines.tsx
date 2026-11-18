"use client";

import { formatCents, formatDelta } from "@/lib/money";
import { placedItemCents, placedItems } from "@/lib/placed";
import type { OrderLineOut } from "@/lib/types";

/** What is already on a placed order, read-only, marking what the kitchen hasn't had. */
export function PlacedLines({ lines }: { lines: OrderLineOut[] }) {
  return (
    <ul className="divide-y divide-border">
      {placedItems(lines).map(({ line, modifiers }) => (
        <li key={line.id} className="flex items-start justify-between gap-3 py-2.5">
          <div className="min-w-0">
            <p className="font-medium leading-tight">
              <span className="text-muted">{line.quantity}×</span> {line.name}
              {!line.sent && (
                <span className="ml-2 rounded bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-950/50 dark:text-amber-300">
                  not sent
                </span>
              )}
            </p>
            {modifiers.length > 0 && (
              <ul className="mt-0.5 text-sm text-muted">
                {modifiers.map((m) => (
                  <li key={m.id}>
                    + {m.quantity > 1 ? `${m.quantity}× ` : ""}
                    {m.name}
                    {m.unit_price_cents !== 0 && ` ${formatDelta(m.unit_price_cents * m.quantity)}`}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <span className="shrink-0 tabular-nums">
            {formatCents(placedItemCents({ line, modifiers }))}
          </span>
        </li>
      ))}
    </ul>
  );
}
