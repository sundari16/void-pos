"use client";

import { formatCents } from "@/lib/money";
import type { MenuItem } from "@/lib/types";

const UNCATEGORIZED = "Other";

function groupByCategory(items: MenuItem[]): [string, MenuItem[]][] {
  const groups = new Map<string, MenuItem[]>();
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

export function MenuGrid({
  items,
  onPick,
}: {
  items: MenuItem[];
  onPick: (item: MenuItem) => void;
}) {
  const available = items.filter((i) => i.is_available);
  if (available.length === 0) {
    return (
      <p className="rounded-xl border border-border bg-surface p-6 text-sm text-muted">
        No available menu items for this location yet.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {groupByCategory(available).map(([category, group]) => (
        <section key={category}>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
            {category}
          </h2>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {group.map((item) => (
              <button
                key={item.id}
                onClick={() => onPick(item)}
                className="flex min-h-20 flex-col justify-between rounded-xl border border-border bg-surface p-3 text-left transition hover:border-accent hover:shadow-sm active:scale-[0.98]"
              >
                <span className="font-medium leading-tight">{item.name}</span>
                <span className="mt-2 text-sm text-muted">
                  {formatCents(item.base_price_cents)}
                </span>
              </button>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
