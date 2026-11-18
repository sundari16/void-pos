"use client";

import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { useAdminLocation } from "@/components/admin/AdminProvider";
import {
  Badge,
  Card,
  ConfirmButton,
  ErrorNote,
  Field,
  inputClass,
  inputErrorClass,
  Notice,
  PageHeader,
  primaryButton,
  secondaryButton,
  smallButton,
  Success,
} from "@/components/admin/ui";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import { checkStaff, pinError, type Errors, type StaffForm } from "@/lib/adminForms";
import type { StaffOut } from "@/lib/adminTypes";

// PINs live only in the input's state until they're sent, and are cleared once the server
// answers. They are never put in storage, the URL or a log.

export default function StaffPage() {
  const { api, location } = useAdminLocation();
  const [staff, setStaff] = useState<StaffOut[] | null>(null);
  const [showInactive, setShowInactive] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setStaff(await api.staff(location.id, showInactive));
      setError(null);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [api, location.id, showInactive]);

  useEffect(() => {
    load();
  }, [load]);

  const hasManager = staff?.some((p) => p.active && p.role === "manager") ?? true;

  return (
    <div className="space-y-4">
      <PageHeader title="Staff">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Show deactivated
        </label>
      </PageHeader>

      {!hasManager && (
        <Notice>
          No active manager yet, so voids and sold-out switches at the tills need no PIN. Add a
          manager before going live.
        </Notice>
      )}

      <AddPerson api={api} locationId={location.id} onAdded={load} />

      {error && <ErrorNote>{error}</ErrorNote>}
      {!staff && !error && <p className="text-sm text-muted">Loading…</p>}
      {staff && (
        <section className="rounded-2xl border border-border bg-surface">
          <h2 className="border-b border-border px-4 py-2.5 font-semibold">
            People <span className="text-sm font-normal text-muted">({staff.length})</span>
          </h2>
          {staff.length === 0 ? (
            <p className="p-4 text-muted">No staff yet.</p>
          ) : (
            <ul className="divide-y divide-border">
              {staff.map((person) => (
                <PersonRow key={person.id} api={api} person={person} onChanged={load} />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

function AddPerson({
  api,
  locationId,
  onAdded,
}: {
  api: AdminApi;
  locationId: string;
  onAdded: () => Promise<void>;
}) {
  const [form, setForm] = useState<StaffForm>({ name: "", role: "staff", pin: "" });
  const [errors, setErrors] = useState<Errors<StaffForm>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkStaff(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setAdded(null);
    setBusy(true);
    try {
      const person = await api.addStaff(locationId, checked.value);
      setForm({ name: "", role: "staff", pin: "" });
      setAdded(`${person.name} can now use their PIN at the tills.`);
      await onAdded();
    } catch (err) {
      setForm((f) => ({ ...f, pin: "" }));
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  const cls = (key: keyof StaffForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;

  return (
    <Card title="Add a person">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <Field label="Name" htmlFor={`${uid}-name`} error={errors.name}>
            <input
              id={`${uid}-name`}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              maxLength={120}
              autoComplete="off"
              className={cls("name")}
            />
          </Field>
          <Field label="Role" htmlFor={`${uid}-role`} error={errors.role}>
            <select
              id={`${uid}-role`}
              value={form.role}
              onChange={(e) =>
                setForm((f) => ({ ...f, role: e.target.value === "manager" ? "manager" : "staff" }))
              }
              className={cls("role")}
            >
              <option value="staff">Staff</option>
              <option value="manager">Manager</option>
            </select>
          </Field>
          <Field label="PIN" htmlFor={`${uid}-pin`} error={errors.pin} hint="4 to 8 digits">
            <PinInput
              id={`${uid}-pin`}
              value={form.pin}
              onChange={(pin) => setForm((f) => ({ ...f, pin }))}
              invalid={!!errors.pin}
            />
          </Field>
        </div>
        <p className="text-xs text-muted">
          Managers approve voids and sold-out switches at the tills. Each PIN is unique at the
          venue.
        </p>
        {serverError && <ErrorNote>{serverError}</ErrorNote>}
        {added && <Success>{added}</Success>}
        <button type="submit" disabled={busy} className={primaryButton}>
          {busy ? "Adding…" : "Add person"}
        </button>
      </form>
    </Card>
  );
}

function PinInput({
  id,
  value,
  onChange,
  invalid,
}: {
  id: string;
  value: string;
  onChange: (pin: string) => void;
  invalid?: boolean;
}) {
  return (
    <input
      id={id}
      type="password"
      inputMode="numeric"
      autoComplete="off"
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/\D/g, "").slice(0, 8))}
      className={`${inputClass} tracking-[0.3em] ${invalid ? inputErrorClass : ""}`}
    />
  );
}

function PersonRow({
  api,
  person,
  onChanged,
}: {
  api: AdminApi;
  person: StaffOut;
  onChanged: () => Promise<void>;
}) {
  const [resetting, setResetting] = useState(false);
  const [pin, setPin] = useState("");
  const [pinProblem, setPinProblem] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  async function resetPin(event: FormEvent) {
    event.preventDefault();
    const problem = pinError(pin);
    if (problem) {
      setPinProblem(problem);
      return;
    }
    setPinProblem(null);
    setError(null);
    setBusy(true);
    try {
      await api.setPin(person.id, pin);
      setResetting(false);
      setMessage("PIN changed.");
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setPin("");
      setBusy(false);
    }
  }

  async function deactivate() {
    setError(null);
    setBusy(true);
    try {
      await api.deactivateStaff(person.id);
      await onChanged();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-[10rem] flex-1">
          <span className={`font-medium ${person.active ? "" : "text-muted line-through"}`}>
            {person.name}
          </span>{" "}
          <Badge tone={person.role === "manager" ? "accent" : "neutral"}>
            {person.role === "manager" ? "Manager" : "Staff"}
          </Badge>{" "}
          {!person.active && <Badge tone="bad">Deactivated</Badge>}
        </div>
        {person.active && !resetting && (
          <div className="flex flex-wrap gap-2">
            <button
              onClick={() => {
                setResetting(true);
                setMessage(null);
              }}
              className={smallButton}
            >
              Reset PIN
            </button>
            <ConfirmButton
              label="Deactivate"
              question={`Stop ${person.name}'s PIN working?`}
              confirmLabel="Deactivate"
              disabled={busy}
              onConfirm={deactivate}
            />
          </div>
        )}
      </div>
      {resetting && (
        <form onSubmit={resetPin} className="mt-3 flex flex-wrap items-end gap-2" noValidate>
          <div className="w-44">
            <label htmlFor={`${uid}-pin`} className="mb-1 block text-sm font-medium">
              New PIN for {person.name}
            </label>
            <PinInput id={`${uid}-pin`} value={pin} onChange={setPin} invalid={!!pinProblem} />
          </div>
          <button type="submit" disabled={busy} className={primaryButton}>
            {busy ? "Saving…" : "Save PIN"}
          </button>
          <button
            type="button"
            onClick={() => {
              setResetting(false);
              setPin("");
              setPinProblem(null);
            }}
            className={secondaryButton}
          >
            Cancel
          </button>
        </form>
      )}
      {pinProblem && <p className="mt-1 text-sm text-red-700 dark:text-red-300">{pinProblem}</p>}
      {error && <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
      {message && <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-300">{message}</p>}
    </li>
  );
}
