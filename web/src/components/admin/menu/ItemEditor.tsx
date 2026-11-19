"use client";

import { useState, type FormEvent } from "react";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import {
  checkItem,
  itemForm,
  itemPatch,
  selectionLabel,
  TAX_RATES,
  type Errors,
  type ItemForm,
} from "@/lib/adminForms";
import type { ModifierGroup, TillMenuItem } from "@/lib/adminTypes";
import {
  Dialog,
  ErrorNote,
  Field,
  inputClass,
  inputErrorClass,
  primaryButton,
  secondaryButton,
  smallButton,
} from "../ui";

/** Add an item, or edit one: name, price in dollars, category, kitchen name, GST, on sale,
 *  daily window, and (once it exists) which modifier groups it offers. */
export function ItemEditor({
  api,
  locationId,
  item,
  categories,
  groups,
  onSaved,
  onClose,
}: {
  api: AdminApi;
  locationId: string;
  item: TillMenuItem | null;
  categories: string[];
  groups: ModifierGroup[];
  onSaved: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [form, setForm] = useState<ItemForm>(() => itemForm(item ?? undefined));
  const [errors, setErrors] = useState<Errors<ItemForm>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const set = <K extends keyof ItemForm>(key: K, value: ItemForm[K]) =>
    setForm((f) => ({ ...f, [key]: value }));
  // A rate other than 10% or 0% (set by an import, say) stays selectable.
  const rates = TAX_RATES.some((r) => Number(r.value) === Number(form.tax_rate))
    ? TAX_RATES
    : [...TAX_RATES, { value: form.tax_rate, label: `${Math.round(Number(form.tax_rate) * 1000) / 10}%` }];

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkItem(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setBusy(true);
    try {
      if (item) {
        const patch = itemPatch(item, checked.value);
        if (Object.keys(patch).length > 0) await api.updateItem(item.id, patch);
      } else {
        await api.createItem(locationId, checked.value);
      }
      await onSaved();
      onClose();
    } catch (err) {
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  const cls = (key: keyof ItemForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;

  return (
    <Dialog title={item ? `Edit ${item.name}` : "Add an item"} onClose={onClose} wide>
      <form onSubmit={submit} className="space-y-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Name" htmlFor="item-name" error={errors.name} className="sm:col-span-2">
            <input
              id="item-name"
              value={form.name}
              onChange={(e) => set("name", e.target.value)}
              maxLength={200}
              autoFocus={!item}
              className={cls("name")}
            />
          </Field>
          <Field label="Price (GST included)" htmlFor="item-price" error={errors.price}>
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted">
                $
              </span>
              <input
                id="item-price"
                value={form.price}
                onChange={(e) => set("price", e.target.value)}
                inputMode="decimal"
                placeholder="12.50"
                className={`${cls("price")} pl-7 tabular-nums`}
              />
            </div>
          </Field>
          <Field label="Tax" htmlFor="item-tax" error={errors.tax_rate}>
            <select
              id="item-tax"
              value={form.tax_rate}
              onChange={(e) => set("tax_rate", e.target.value)}
              className={cls("tax_rate")}
            >
              {rates.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Category" htmlFor="item-category" error={errors.category}>
            <input
              id="item-category"
              value={form.category}
              onChange={(e) => set("category", e.target.value)}
              list="item-categories"
              maxLength={120}
              placeholder="Mains"
              className={cls("category")}
            />
            <datalist id="item-categories">
              {categories.map((c) => (
                <option key={c} value={c} />
              ))}
            </datalist>
          </Field>
          <Field
            label="Kitchen name"
            htmlFor="item-kitchen"
            error={errors.kitchen_name}
            hint="What the kitchen ticket prints, if not the name."
          >
            <input
              id="item-kitchen"
              value={form.kitchen_name}
              onChange={(e) => set("kitchen_name", e.target.value)}
              maxLength={200}
              className={cls("kitchen_name")}
            />
          </Field>
        </div>

        <fieldset className="rounded-xl border border-border p-3">
          <legend className="px-1 text-sm font-medium">When it&apos;s on sale</legend>
          <label className="flex items-center gap-2 py-1">
            <input
              type="checkbox"
              checked={form.is_available}
              onChange={(e) => set("is_available", e.target.checked)}
              className="h-5 w-5 accent-[var(--accent)]"
            />
            <span>Available (untick when it&apos;s sold out)</span>
          </label>
          <div className="mt-2 grid grid-cols-2 gap-3">
            <Field label="From" htmlFor="item-from" error={errors.available_from}>
              <input
                id="item-from"
                type="time"
                value={form.available_from}
                onChange={(e) => set("available_from", e.target.value)}
                className={cls("available_from")}
              />
            </Field>
            <Field label="Until" htmlFor="item-to" error={errors.available_to}>
              <input
                id="item-to"
                type="time"
                value={form.available_to}
                onChange={(e) => set("available_to", e.target.value)}
                className={cls("available_to")}
              />
            </Field>
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-muted">
              Venue local time. Leave both blank to sell all day; an end before the start runs
              past midnight.
            </p>
            {(form.available_from || form.available_to) && (
              <button
                type="button"
                onClick={() => setForm((f) => ({ ...f, available_from: "", available_to: "" }))}
                className={smallButton}
              >
                All day
              </button>
            )}
          </div>
        </fieldset>

        {serverError && <ErrorNote>{serverError}</ErrorNote>}

        <div className="flex flex-wrap gap-2">
          <button type="submit" disabled={busy} className={primaryButton}>
            {busy ? "Saving…" : item ? "Save changes" : "Add item"}
          </button>
          <button type="button" onClick={onClose} className={secondaryButton}>
            Cancel
          </button>
        </div>
      </form>

      {item && <ItemGroups api={api} item={item} groups={groups} onChanged={onSaved} />}
    </Dialog>
  );
}

/** The modifier groups an item offers, and attaching another. */
function ItemGroups({
  api,
  item,
  groups,
  onChanged,
}: {
  api: AdminApi;
  item: TillMenuItem;
  groups: ModifierGroup[];
  onChanged: () => Promise<void> | void;
}) {
  const attached = groups.filter((g) => item.modifier_group_ids.includes(g.id));
  const others = groups.filter((g) => !item.modifier_group_ids.includes(g.id));
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function attach() {
    if (!pick) return;
    setBusy(true);
    setError(null);
    try {
      await api.attachGroup(item.id, pick);
      setPick("");
      await onChanged();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mt-6 border-t border-border pt-4">
      <h3 className="text-sm font-semibold">Modifier groups</h3>
      {attached.length === 0 ? (
        <p className="mt-1 text-sm text-muted">No modifier groups on this item.</p>
      ) : (
        <ul className="mt-2 flex flex-wrap gap-2">
          {attached.map((g) => (
            <li key={g.id} className="rounded-lg bg-background px-3 py-1.5 text-sm">
              <span className="font-medium">{g.name}</span>{" "}
              <span className="text-muted">· {selectionLabel(g.min_select, g.max_select)}</span>
            </li>
          ))}
        </ul>
      )}
      {others.length > 0 && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <label htmlFor="attach-group" className="sr-only">
            Modifier group to add
          </label>
          <select
            id="attach-group"
            value={pick}
            onChange={(e) => setPick(e.target.value)}
            className={`${inputClass} w-auto min-w-[12rem] flex-1`}
          >
            <option value="">Add a modifier group…</option>
            {others.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <button type="button" onClick={attach} disabled={!pick || busy} className={smallButton}>
            {busy ? "Adding…" : "Add group"}
          </button>
        </div>
      )}
      {error && (
        <div className="mt-2">
          <ErrorNote>{error}</ErrorNote>
        </div>
      )}
    </section>
  );
}
