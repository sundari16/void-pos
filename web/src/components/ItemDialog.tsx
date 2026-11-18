"use client";

import { useState } from "react";
import { formatCents, formatDelta } from "@/lib/money";
import {
  chosenCount,
  chosenModifiers,
  groupOnSale,
  limitLabel,
  selectionProblems,
  setCount,
  toggle,
  windowLabel,
  type ChosenModifier,
  type Selection,
} from "@/lib/menu";
import type { ModifierGroup, TillMenuItem } from "@/lib/types";

export function ItemDialog({
  item,
  groups,
  now,
  onAdd,
  onClose,
}: {
  item: TillMenuItem;
  groups: ModifierGroup[];
  /** Local time at the location, "HH:MM:SS". */
  now: string;
  onAdd: (item: TillMenuItem, quantity: number, chosen: ChosenModifier[], note: string) => void;
  onClose: () => void;
}) {
  const [quantity, setQuantity] = useState(1);
  const [selection, setSelection] = useState<Selection>({});
  const [note, setNote] = useState("");

  const chosen = chosenModifiers(groups, selection);
  const modsEach = chosen.reduce((sum, c) => sum + c.modifier.price_delta_cents * c.quantity, 0);
  const subtotal = (item.base_price_cents + modsEach) * quantity;
  const problems = selectionProblems(groups, selection, now);

  return (
    <div
      className="fixed inset-0 z-40 grid place-items-end bg-black/40 p-0 sm:place-items-center sm:p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-label={item.name}
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
            <Stepper
              value={quantity}
              label="quantity"
              onChange={(q) => setQuantity(Math.max(1, Math.min(999, q)))}
            />
          </div>

          {groups.map((group) => (
            <GroupPicker
              key={group.id}
              group={group}
              onSale={groupOnSale(group, now)}
              selection={selection}
              onChange={setSelection}
            />
          ))}

          <div>
            <label
              htmlFor="kitchen-note"
              className="mb-2 block text-xs font-semibold uppercase tracking-wide text-muted"
            >
              Note for the kitchen
            </label>
            <input
              id="kitchen-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={200}
              autoComplete="off"
              placeholder="e.g. nut allergy"
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
            />
          </div>
        </div>

        <div className="space-y-2 border-t border-border p-4">
          {problems.length > 0 && (
            <ul className="text-sm text-amber-700 dark:text-amber-400">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          <button
            onClick={() => onAdd(item, quantity, chosen, note)}
            disabled={problems.length > 0}
            className="flex w-full items-center justify-between rounded-xl bg-accent px-4 py-3 font-semibold text-white hover:opacity-95 active:scale-[0.99] disabled:opacity-40"
          >
            <span>Add to order</span>
            <span className="tabular-nums">{formatCents(subtotal)}</span>
          </button>
        </div>
      </div>
    </div>
  );
}

function GroupPicker({
  group,
  onSale,
  selection,
  onChange,
}: {
  group: ModifierGroup;
  onSale: boolean;
  selection: Selection;
  onChange: (next: Selection) => void;
}) {
  const multiple = group.max_select !== 1;
  const full = group.max_select !== null && chosenCount(group, selection) >= group.max_select;
  const hours = windowLabel(group.available_from, group.available_to);

  return (
    <fieldset disabled={!onSale} className={onSale ? "" : "opacity-50"}>
      <legend className="mb-2 flex w-full items-baseline justify-between gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">{group.name}</span>
        <span className="text-xs text-muted">
          {onSale ? limitLabel(group) : `Not available now${hours ? ` (${hours})` : ""}`}
        </span>
      </legend>
      <div className="flex flex-wrap gap-2">
        {group.modifiers.map((mod) => {
          const count = selection[mod.id] ?? 0;
          const price = mod.price_delta_cents !== 0 && (
            <span className={count ? "ml-1 opacity-90" : "ml-1 text-muted"}>
              {formatDelta(mod.price_delta_cents)}
            </span>
          );
          if (count && multiple) {
            return (
              <span
                key={mod.id}
                className="inline-flex items-center rounded-full border border-accent bg-accent text-sm text-white"
              >
                <button
                  onClick={() => onChange(setCount(group, selection, mod.id, count - 1))}
                  aria-label={`One less ${mod.name}`}
                  className="px-2.5 py-1.5"
                >
                  −
                </button>
                <span>
                  {count > 1 ? `${count}× ` : ""}
                  {mod.name}
                  {price}
                </span>
                <button
                  onClick={() => onChange(setCount(group, selection, mod.id, count + 1))}
                  disabled={full}
                  aria-label={`One more ${mod.name}`}
                  className="px-2.5 py-1.5 disabled:opacity-40"
                >
                  +
                </button>
              </span>
            );
          }
          return (
            <button
              key={mod.id}
              onClick={() => onChange(toggle(group, selection, mod.id))}
              disabled={!count && full && multiple}
              aria-pressed={count > 0}
              className={`rounded-full border px-3 py-1.5 text-sm transition disabled:opacity-40 ${
                count ? "border-accent bg-accent text-white" : "border-border bg-surface hover:border-accent"
              }`}
            >
              {mod.name}
              {price}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function Stepper({
  value,
  label,
  onChange,
}: {
  value: number;
  label: string;
  onChange: (value: number) => void;
}) {
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => onChange(value - 1)}
        className="grid h-9 w-9 place-items-center rounded-lg border border-border text-lg hover:bg-background"
        aria-label={`Decrease ${label}`}
      >
        −
      </button>
      <span className="w-6 text-center font-semibold tabular-nums">{value}</span>
      <button
        onClick={() => onChange(value + 1)}
        className="grid h-9 w-9 place-items-center rounded-lg border border-border text-lg hover:bg-background"
        aria-label={`Increase ${label}`}
      >
        +
      </button>
    </div>
  );
}
