"use client";

import { useState } from "react";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import { windowLabel } from "@/lib/adminForms";
import { groupByCategory } from "@/lib/adminMenu";
import type { TillMenu, TillMenuItem } from "@/lib/adminTypes";
import { formatCents } from "@/lib/money";
import { Badge, ConfirmButton, smallButton } from "../ui";

/** Every item, grouped by category, with a quick sold-out switch, edit and delete. */
export function ItemsList({
  api,
  menu,
  onEdit,
  onChanged,
}: {
  api: AdminApi;
  menu: TillMenu;
  onEdit: (item: TillMenuItem) => void;
  onChanged: () => Promise<void>;
}) {
  const sections = groupByCategory(menu.items);
  const groupNames = new Map(menu.modifier_groups.map((g) => [g.id, g.name]));

  if (menu.items.length === 0) {
    return <p className="rounded-2xl border border-border bg-surface p-6 text-muted">No items yet. Add the first one.</p>;
  }

  return (
    <div className="space-y-4">
      {sections.map((section) => (
        <section key={section.category} className="rounded-2xl border border-border bg-surface">
          <h2 className="border-b border-border px-4 py-2.5 font-semibold">
            {section.category}{" "}
            <span className="text-sm font-normal text-muted">({section.items.length})</span>
          </h2>
          <ul className="divide-y divide-border">
            {section.items.map((item) => (
              <ItemRow
                key={item.id}
                api={api}
                item={item}
                groupNames={groupNames}
                onEdit={() => onEdit(item)}
                onChanged={onChanged}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ItemRow({
  api,
  item,
  groupNames,
  onEdit,
  onChanged,
}: {
  api: AdminApi;
  item: TillMenuItem;
  groupNames: Map<string, string>;
  onEdit: () => void;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hours = windowLabel(item.available_from, item.available_to);
  const groups = item.modifier_group_ids.map((id) => groupNames.get(id)).filter(Boolean);

  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await action();
      await onChanged();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-[11rem] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`font-medium ${item.is_available ? "" : "text-muted line-through"}`}>
              {item.name}
            </span>
            <span className="font-semibold tabular-nums">{formatCents(item.base_price_cents)}</span>
            {!item.is_available && <Badge tone="bad">Sold out</Badge>}
            {item.is_available && !item.available_now && <Badge tone="warn">Not on sale now</Badge>}
            {item.tax_rate === 0 && <Badge>GST free</Badge>}
          </div>
          {(item.kitchen_name || hours || groups.length > 0) && (
            <p className="mt-0.5 text-sm text-muted">
              {[
                item.kitchen_name && `Kitchen: ${item.kitchen_name}`,
                hours && `Sold ${hours}`,
                groups.length > 0 && `Options: ${groups.join(", ")}`,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => run(() => api.setAvailable(item.id, !item.is_available))}
            disabled={busy}
            aria-pressed={!item.is_available}
            className={
              item.is_available
                ? smallButton
                : "rounded-lg bg-accent px-3 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
            }
          >
            {item.is_available ? "Sold out" : "Back on sale"}
          </button>
          <button onClick={onEdit} disabled={busy} className={smallButton}>
            Edit
          </button>
          <ConfirmButton
            label="Delete"
            question={`Delete ${item.name}?`}
            confirmLabel="Delete"
            disabled={busy}
            onConfirm={() => run(() => api.deleteItem(item.id))}
          />
        </div>
      </div>
      {error && <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
    </li>
  );
}
