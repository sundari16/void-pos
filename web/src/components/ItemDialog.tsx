"use client";

import { useMemo, useState } from "react";
import { formatCents, formatDelta } from "@/lib/money";
import { QUICK_MODIFIER_GROUPS, type QuickModifier } from "@/lib/modifiers";
import type { MenuItem } from "@/lib/types";

export function ItemDialog({
  item,
  onAdd,
  onClose,
}: {
  item: MenuItem;
  onAdd: (item: MenuItem, quantity: number, modifiers: QuickModifier[]) => void;
  onClose: () => void;
}) {
  const [quantity, setQuantity] = useState(1);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const chosen = useMemo(() => {
    const all = QUICK_MODIFIER_GROUPS.flatMap((g) => g.modifiers);
    return all.filter((m) => selected.has(m.key));
  }, [selected]);

  const modsEach = chosen.reduce((sum, m) => sum + m.priceDeltaCents, 0);
  const subtotal = item.base_price_cents * quantity + modsEach;

  function toggle(key: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-end bg-black/40 p-0 sm:place-items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85dvh] w-full max-w-md flex-col overflow-hidden rounded-t-2xl bg-surface shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-border p-4">
          <div>
            <h2 className="text-lg font-semibold">{item.name}</h2>
            <p className="text-sm text-muted">{formatCents(item.base_price_cents)}</p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="rounded-md px-2 py-1 text-xl leading-none text-muted hover:bg-background"
          >
            ×
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto p-4">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Quantity</span>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setQuantity((q) => Math.max(1, q - 1))}
                className="grid h-9 w-9 place-items-center rounded-lg border border-border text-lg hover:bg-background"
                aria-label="Decrease quantity"
              >
                −
              </button>
              <span className="w-6 text-center font-semibold tabular-nums">{quantity}</span>
              <button
                onClick={() => setQuantity((q) => q + 1)}
                className="grid h-9 w-9 place-items-center rounded-lg border border-border text-lg hover:bg-background"
                aria-label="Increase quantity"
              >
                +
              </button>
            </div>
          </div>

          {QUICK_MODIFIER_GROUPS.map((groupDef) => (
            <div key={groupDef.key}>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
                {groupDef.name}
              </h3>
              <div className="flex flex-wrap gap-2">
                {groupDef.modifiers.map((mod) => {
                  const on = selected.has(mod.key);
                  return (
                    <button
                      key={mod.key}
                      onClick={() => toggle(mod.key)}
                      className={`rounded-full border px-3 py-1.5 text-sm transition ${
                        on
                          ? "border-accent bg-accent text-white"
                          : "border-border bg-surface hover:border-accent"
                      }`}
                    >
                      {mod.name}
                      {mod.priceDeltaCents !== 0 && (
                        <span className={on ? "ml-1 opacity-90" : "ml-1 text-muted"}>
                          {formatDelta(mod.priceDeltaCents)}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <div className="border-t border-border p-4">
          <button
            onClick={() => onAdd(item, quantity, chosen)}
            className="flex w-full items-center justify-between rounded-xl bg-accent px-4 py-3 font-semibold text-white hover:opacity-95 active:scale-[0.99]"
          >
            <span>Add to order</span>
            <span className="tabular-nums">{formatCents(subtotal)}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
