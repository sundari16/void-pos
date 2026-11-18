"use client";

import { useId, useState, type FormEvent } from "react";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import {
  checkGroup,
  checkModifier,
  groupForm,
  groupPatch,
  modifierForm,
  modifierPatch,
  selectionLabel,
  windowLabel,
  type Errors,
  type GroupForm,
  type ModifierForm,
} from "@/lib/adminForms";
import type { Modifier, ModifierGroup, TillMenu } from "@/lib/adminTypes";
import { formatCents, formatDelta } from "@/lib/money";
import {
  Badge,
  Card,
  ErrorNote,
  Field,
  inputClass,
  inputErrorClass,
  primaryButton,
  secondaryButton,
  smallButton,
} from "../ui";

type Changed = () => Promise<void>;

/** Modifier groups with their modifiers: create and edit both, and put a group on items. */
export function ModifierGroups({
  api,
  locationId,
  menu,
  onChanged,
}: {
  api: AdminApi;
  locationId: string;
  menu: TillMenu;
  onChanged: Changed;
}) {
  const [adding, setAdding] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted">
          Choices the till offers with an item, like spice level or extras.
        </p>
        {!adding && (
          <button onClick={() => setAdding(true)} className={primaryButton}>
            New group
          </button>
        )}
      </div>

      {adding && (
        <Card title="New modifier group">
          <GroupFields
            submitLabel="Create group"
            onCancel={() => setAdding(false)}
            onSubmit={async (value) => {
              await api.createGroup(locationId, value);
              await onChanged();
              setAdding(false);
            }}
          />
        </Card>
      )}

      {menu.modifier_groups.length === 0 && !adding && (
        <p className="rounded-2xl border border-border bg-surface p-6 text-muted">
          No modifier groups yet.
        </p>
      )}

      {menu.modifier_groups.map((group) => (
        <GroupCard key={group.id} api={api} group={group} menu={menu} onChanged={onChanged} />
      ))}
    </div>
  );
}

function GroupCard({
  api,
  group,
  menu,
  onChanged,
}: {
  api: AdminApi;
  group: ModifierGroup;
  menu: TillMenu;
  onChanged: Changed;
}) {
  const [editing, setEditing] = useState(false);
  const items = menu.items.filter((i) => i.modifier_group_ids.includes(group.id));
  const hours = windowLabel(group.available_from, group.available_to);

  return (
    <section className="rounded-2xl border border-border bg-surface p-4">
      {editing ? (
        <GroupFields
          initial={groupForm(group)}
          submitLabel="Save group"
          onCancel={() => setEditing(false)}
          onSubmit={async (value) => {
            const patch = groupPatch(group, value);
            if (Object.keys(patch).length > 0) await api.updateGroup(group.id, patch);
            await onChanged();
            setEditing(false);
          }}
        />
      ) : (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <h2 className="flex flex-wrap items-center gap-2 font-semibold">
              {group.name}
              <Badge tone="accent">{selectionLabel(group.min_select, group.max_select)}</Badge>
              {hours && <Badge tone={group.available_now ? "neutral" : "warn"}>{hours}</Badge>}
            </h2>
            <p className="mt-0.5 text-sm text-muted">
              {items.length === 0
                ? "Not on any item yet."
                : `On ${items.map((i) => i.name).join(", ")}`}
            </p>
          </div>
          <button onClick={() => setEditing(true)} className={smallButton}>
            Edit group
          </button>
        </div>
      )}

      <ul className="mt-3 divide-y divide-border rounded-xl border border-border">
        {group.modifiers.length === 0 && (
          <li className="px-3 py-2 text-sm text-muted">No modifiers in this group yet.</li>
        )}
        {group.modifiers.map((m) => (
          <ModifierRow key={m.id} api={api} modifier={m} onChanged={onChanged} />
        ))}
      </ul>

      <AddModifier api={api} groupId={group.id} onChanged={onChanged} />
      <AttachToItem api={api} group={group} menu={menu} onChanged={onChanged} />
    </section>
  );
}

function GroupFields({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: GroupForm;
  submitLabel: string;
  onSubmit: (value: Parameters<typeof groupPatch>[1]) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<GroupForm>(initial ?? groupForm());
  const [errors, setErrors] = useState<Errors<GroupForm>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof GroupForm, value: string) => setForm((f) => ({ ...f, [key]: value }));
  const cls = (key: keyof GroupForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;
  const uid = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkGroup(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setBusy(true);
    try {
      await onSubmit(checked.value);
    } catch (err) {
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Group name" htmlFor={`${uid}-name`} error={errors.name} className="sm:col-span-2">
          <input
            id={`${uid}-name`}
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            maxLength={200}
            placeholder="Spice level"
            className={cls("name")}
          />
        </Field>
        <Field
          label="Fewest choices"
          htmlFor={`${uid}-min`}
          error={errors.min_select}
          hint="0 makes the group optional."
        >
          <input
            id={`${uid}-min`}
            value={form.min_select}
            onChange={(e) => set("min_select", e.target.value)}
            inputMode="numeric"
            className={cls("min_select")}
          />
        </Field>
        <Field
          label="Most choices"
          htmlFor={`${uid}-max`}
          error={errors.max_select}
          hint="Blank for no limit; 1 makes it pick-one."
        >
          <input
            id={`${uid}-max`}
            value={form.max_select}
            onChange={(e) => set("max_select", e.target.value)}
            inputMode="numeric"
            className={cls("max_select")}
          />
        </Field>
        <Field label="Offered from" htmlFor={`${uid}-from`} error={errors.available_from}>
          <input
            id={`${uid}-from`}
            type="time"
            value={form.available_from}
            onChange={(e) => set("available_from", e.target.value)}
            className={cls("available_from")}
          />
        </Field>
        <Field label="Until" htmlFor={`${uid}-to`} error={errors.available_to}>
          <input
            id={`${uid}-to`}
            type="time"
            value={form.available_to}
            onChange={(e) => set("available_to", e.target.value)}
            className={cls("available_to")}
          />
        </Field>
      </div>
      {serverError && <ErrorNote>{serverError}</ErrorNote>}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={primaryButton}>
          {busy ? "Saving…" : submitLabel}
        </button>
        <button type="button" onClick={onCancel} className={secondaryButton}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ModifierRow({
  api,
  modifier,
  onChanged,
}: {
  api: AdminApi;
  modifier: Modifier;
  onChanged: Changed;
}) {
  const [editing, setEditing] = useState(false);
  if (editing) {
    return (
      <li className="p-3">
        <ModifierFields
          initial={modifierForm(modifier)}
          submitLabel="Save"
          onCancel={() => setEditing(false)}
          onSubmit={async (value) => {
            const patch = modifierPatch(modifier, value);
            if (Object.keys(patch).length > 0) await api.updateModifier(modifier.id, patch);
            await onChanged();
            setEditing(false);
          }}
        />
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center gap-3 px-3 py-2">
      <span className="min-w-[8rem] flex-1">
        {modifier.name}
        {modifier.kitchen_name && (
          <span className="ml-2 text-sm text-muted">Kitchen: {modifier.kitchen_name}</span>
        )}
      </span>
      <span className="tabular-nums text-muted">
        {formatDelta(modifier.price_delta_cents) || `${formatCents(0)}`}
      </span>
      <button onClick={() => setEditing(true)} className={smallButton}>
        Edit
      </button>
    </li>
  );
}

function AddModifier({
  api,
  groupId,
  onChanged,
}: {
  api: AdminApi;
  groupId: string;
  onChanged: Changed;
}) {
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState(0); // remount to clear the form after each add
  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className={`mt-3 ${smallButton}`}>
        Add modifier
      </button>
    );
  }
  return (
    <div className="mt-3 rounded-xl bg-background p-3">
      <ModifierFields
        key={key}
        initial={modifierForm()}
        submitLabel="Add modifier"
        onCancel={() => setOpen(false)}
        onSubmit={async (value) => {
          await api.createModifier(groupId, value);
          await onChanged();
          setKey((k) => k + 1);
        }}
      />
    </div>
  );
}

function ModifierFields({
  initial,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: ModifierForm;
  submitLabel: string;
  onSubmit: (value: Parameters<typeof modifierPatch>[1]) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<ModifierForm>(initial);
  const [errors, setErrors] = useState<Errors<ModifierForm>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof ModifierForm, value: string) => setForm((f) => ({ ...f, [key]: value }));
  const cls = (key: keyof ModifierForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;
  const uid = useId();
  const idFor = (name: string) => `${uid}-${name}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkModifier(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setBusy(true);
    try {
      await onSubmit(checked.value);
    } catch (err) {
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Modifier" htmlFor={idFor("mod-name")} error={errors.name}>
          <input
            id={idFor("mod-name")}
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            maxLength={200}
            placeholder="Extra butter"
            className={cls("name")}
          />
        </Field>
        <Field
          label="Price change ($)"
          htmlFor={idFor("mod-price")}
          error={errors.price}
          hint="Use a minus to take money off."
        >
          <input
            id={idFor("mod-price")}
            value={form.price}
            onChange={(e) => set("price", e.target.value)}
            inputMode="decimal"
            className={`${cls("price")} tabular-nums`}
          />
        </Field>
        <Field label="Kitchen name" htmlFor={idFor("mod-kitchen")} error={errors.kitchen_name}>
          <input
            id={idFor("mod-kitchen")}
            value={form.kitchen_name}
            onChange={(e) => set("kitchen_name", e.target.value)}
            maxLength={200}
            className={cls("kitchen_name")}
          />
        </Field>
      </div>
      {serverError && <ErrorNote>{serverError}</ErrorNote>}
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={primaryButton}>
          {busy ? "Saving…" : submitLabel}
        </button>
        <button type="button" onClick={onCancel} className={secondaryButton}>
          {submitLabel.startsWith("Add") ? "Done" : "Cancel"}
        </button>
      </div>
    </form>
  );
}

function AttachToItem({
  api,
  group,
  menu,
  onChanged,
}: {
  api: AdminApi;
  group: ModifierGroup;
  menu: TillMenu;
  onChanged: Changed;
}) {
  const candidates = menu.items.filter((i) => !i.modifier_group_ids.includes(group.id));
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (candidates.length === 0) return null;
  const selectId = `attach-${group.id}`;

  async function attach() {
    if (!pick) return;
    setBusy(true);
    setError(null);
    try {
      await api.attachGroup(pick, group.id);
      setPick("");
      await onChanged();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mt-3 border-t border-border pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor={selectId} className="text-sm text-muted">
          Offer on
        </label>
        <select
          id={selectId}
          value={pick}
          onChange={(e) => setPick(e.target.value)}
          className={`${inputClass} w-auto min-w-[12rem] flex-1`}
        >
          <option value="">Pick an item…</option>
          {candidates.map((i) => (
            <option key={i.id} value={i.id}>
              {i.name}
            </option>
          ))}
        </select>
        <button onClick={attach} disabled={!pick || busy} className={smallButton}>
          {busy ? "Adding…" : "Add to item"}
        </button>
      </div>
      {error && <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
    </div>
  );
}
