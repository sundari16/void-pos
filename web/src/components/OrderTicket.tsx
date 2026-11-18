"use client";

import { formatCents, formatDelta } from "@/lib/money";
import { itemSubtotalCents, type DraftItem } from "@/lib/order";

export function OrderTicket({
  items,
  editable,
  onQtyChange,
  onRemove,
}: {
  items: DraftItem[];
  editable: boolean;
  onQtyChange: (ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="px-1 py-10 text-center text-sm text-muted">
        Tap a menu item to start the order.
      </p>
    );
  }

  return (
    <ul className="divide-y divide-border">
      {items.map((item) => (
        <li key={item.ref} className="py-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <p className="font-medium leading-tight">{item.name}</p>
              {item.modifiers.length > 0 && (
                <ul className="mt-1 space-y-0.5 text-sm text-muted">
                  {item.modifiers.map((mod) => (
                    <li key={mod.ref} className="flex gap-1">
                      <span>
                        + {mod.quantity > 1 ? `${mod.quantity}× ` : ""}
                        {mod.name}
                      </span>
                      {mod.unitPriceCents !== 0 && (
                        <span>{formatDelta(mod.unitPriceCents * mod.quantity)}</span>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <span className="shrink-0 tabular-nums">{formatCents(itemSubtotalCents(item))}</span>
          </div>

          <div className="mt-2 flex items-center gap-3">
            {editable ? (
              <>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => onQtyChange(item.ref, -1)}
                    className="grid h-7 w-7 place-items-center rounded-md border border-border hover:bg-background"
                    aria-label={`Decrease ${item.name}`}
                  >
                    −
                  </button>
                  <span className="w-5 text-center text-sm tabular-nums">{item.quantity}</span>
                  <button
                    onClick={() => onQtyChange(item.ref, 1)}
                    className="grid h-7 w-7 place-items-center rounded-md border border-border hover:bg-background"
                    aria-label={`Increase ${item.name}`}
                  >
                    +
                  </button>
                </div>
                <button
                  onClick={() => onRemove(item.ref)}
                  className="text-sm text-red-500 hover:underline"
                >
                  Remove
                </button>
              </>
            ) : (
              <span className="text-sm text-muted">Qty {item.quantity}</span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
