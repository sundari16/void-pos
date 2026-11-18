"use client";

import { itemOnSale, windowLabel } from "@/lib/menu";
import { formatCents } from "@/lib/money";
import type { TillMenuItem } from "@/lib/types";

const UNCATEGORIZED = "Other";

function groupByCategory(items: TillMenuItem[]): [string, TillMenuItem[]][] {
  const groups = new Map<string, TillMenuItem[]>();
  for (const item of items) {
    const key = item.category?.trim() || UNCATEGORIZED;
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (a === UNCATEGORIZED) return 1;
    if (b === UNCATEGORIZED) return -1;
    return a.localeCompare(b);
  });
}

/** Every item, by category. Items not on sale now stay visible, greyed out with why, so
 *  staff can answer "do you have the thali?" without hunting. */
export function MenuGrid({
  items,
  now,
  onPick,
}: {
  items: TillMenuItem[];
  /** Local time at the location, "HH:MM:SS". */
  now: string;
  onPick: (item: TillMenuItem) => void;
}) {
  if (items.length === 0) {
    return (
      <p className="rounded-xl border border-border bg-surface p-6 text-sm text-muted">
        No menu items for this location yet.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {groupByCategory(items).map(([category, group]) => (
        <section key={category}>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {category}
          </h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {group.map((item) => {
              const onSale = itemOnSale(item, now);
              const why = !item.is_available
                ? "Unavailable"
                : windowLabel(item.available_from, item.available_to);
              return (
                <button
                  key={item.id}
                  onClick={() => onPick(item)}
                  disabled={!onSale}
                  className="flex min-h-20 flex-col justify-between rounded-xl border border-border bg-surface p-3 text-left transition hover:border-accent hover:shadow-sm active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-border disabled:hover:shadow-none"
                >
                  <span className="font-medium leading-tight">{item.name}</span>
                  <span className="mt-2 flex items-baseline justify-between gap-2 text-sm text-muted">
                    <span>{formatCents(item.base_price_cents)}</span>
                    {!onSale && why && <span className="text-xs">{why}</span>}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      ))}
    </div>
  );
}
