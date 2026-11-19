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
  PageHeader,
  primaryButton,
  secondaryButton,
  smallButton,
} from "@/components/admin/ui";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import { checkDevice, suggestRange, type DeviceForm, type Errors } from "@/lib/adminForms";
import { formatClock, formatDateTime, timeLeft } from "@/lib/adminSession";
import type { DeviceOut, PairingCodeOut } from "@/lib/adminTypes";

interface ShownCode extends PairingCodeOut {
  tillName: string;
}

export default function TillsPage() {
  const { api, location } = useAdminLocation();
  const [devices, setDevices] = useState<DeviceOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [code, setCode] = useState<ShownCode | null>(null);

  const load = useCallback(async () => {
    try {
      setDevices(await api.devices(location.id));
      setError(null);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [api, location.id]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-4">
      <PageHeader title="Tills">
        <button onClick={load} className={smallButton}>
          Refresh
        </button>
      </PageHeader>

      {code && (
        <PairingCode code={code} timezone={location.timezone} onDone={() => setCode(null)} />
      )}

      {error && <ErrorNote>{error}</ErrorNote>}
      {!devices && !error && <p className="text-sm text-muted">Loading…</p>}
      {devices && (
        <section className="rounded-2xl border border-border bg-surface">
          <h2 className="border-b border-border px-4 py-2.5 font-semibold">
            Devices <span className="text-sm font-normal text-muted">({devices.length})</span>
          </h2>
          {devices.length === 0 ? (
            <p className="p-4 text-muted">No tills yet. Add the first one below.</p>
          ) : (
            <ul className="divide-y divide-border">
              {devices.map((device) => (
                <DeviceRow
                  key={device.id}
                  api={api}
                  device={device}
                  timezone={location.timezone}
                  onCode={(c) => setCode({ ...c, tillName: device.name })}
                  onChanged={load}
                />
              ))}
            </ul>
          )}
        </section>
      )}

      {devices && (
        <AddTill
          api={api}
          locationId={location.id}
          devices={devices}
          onAdded={async (created) => {
            setCode({
              pairing_code: created.pairing_code,
              expires_at: created.expires_at,
              tillName: created.device.name,
            });
            await load();
          }}
        />
      )}
    </div>
  );
}

/** The one-time pairing code, big enough to read across the counter, with its expiry. */
function PairingCode({
  code,
  timezone,
  onDone,
}: {
  code: ShownCode;
  timezone: string;
  onDone: () => void;
}) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);
  const left = timeLeft(code.expires_at, now);

  return (
    <section
      aria-live="polite"
      className="rounded-2xl border-2 border-accent bg-surface p-6 text-center shadow-sm"
    >
      <p className="text-sm font-medium text-muted">Pairing code for {code.tillName}</p>
      <p
        className={`my-3 select-all font-mono text-5xl font-bold tracking-[0.15em] sm:text-6xl ${
          left ? "" : "text-muted line-through"
        }`}
      >
        {code.pairing_code}
      </p>
      <p className="text-sm">
        {left ? (
          <>
            Expires at {formatClock(code.expires_at, timezone)} ·{" "}
            <span className="font-semibold tabular-nums">{left}</span> left
          </>
        ) : (
          <span className="font-semibold text-red-700 dark:text-red-300">
            Expired. Issue a new code.
          </span>
        )}
      </p>
      <p className="mt-2 text-sm text-muted">
        Open Void POS on the tablet and enter this code on its pairing screen. It works once.
      </p>
      <button onClick={onDone} className={`mt-4 ${secondaryButton}`}>
        Done
      </button>
    </section>
  );
}

function DeviceRow({
  api,
  device,
  timezone,
  onCode,
  onChanged,
}: {
  api: AdminApi;
  device: DeviceOut;
  timezone: string;
  onCode: (code: PairingCodeOut) => void;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revoked = device.revoked_at !== null;

  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-[12rem] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`font-medium ${revoked ? "text-muted line-through" : ""}`}>
              {device.name}
            </span>
            {revoked ? (
              <Badge tone="bad">Revoked</Badge>
            ) : device.paired ? (
              <Badge tone="good">Paired</Badge>
            ) : (
              <Badge tone="warn">Not paired</Badge>
            )}
          </div>
          <p className="mt-0.5 text-sm text-muted">
            Orders {device.order_number_start}–{device.order_number_end}
            {revoked && device.revoked_at && ` · revoked ${formatDateTime(device.revoked_at, timezone)}`}
            {!revoked && device.paired_at && ` · paired ${formatDateTime(device.paired_at, timezone)}`}
          </p>
        </div>
        {!revoked && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => run(async () => onCode(await api.pairingCode(device.id)))}
              disabled={busy}
              className={smallButton}
            >
              {device.paired ? "Re-pair" : "Pairing code"}
            </button>
            <ConfirmButton
              label="Revoke"
              question={`Cut ${device.name} off for good?`}
              confirmLabel="Revoke"
              disabled={busy}
              onConfirm={() =>
                run(async () => {
                  await api.revokeDevice(device.id);
                  await onChanged();
                })
              }
            />
          </div>
        )}
      </div>
      {device.paired && !revoked && (
        <p className="mt-1 text-xs text-muted">
          A new code replaces this till&apos;s pairing once it&apos;s used.
        </p>
      )}
      {error && <p className="mt-2 text-sm text-red-700 dark:text-red-300">{error}</p>}
    </li>
  );
}

function AddTill({
  api,
  locationId,
  devices,
  onAdded,
}: {
  api: AdminApi;
  locationId: string;
  devices: DeviceOut[];
  onAdded: (created: Awaited<ReturnType<AdminApi["addDevice"]>>) => Promise<void>;
}) {
  const fresh = (): DeviceForm => {
    const range = suggestRange(devices);
    return { name: `Till ${devices.length + 1}`, start: String(range.start), end: String(range.end) };
  };
  const [form, setForm] = useState<DeviceForm>(fresh);
  const [errors, setErrors] = useState<Errors<DeviceForm>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const uid = useId();
  const cls = (key: keyof DeviceForm) => `${inputClass} ${errors[key] ? inputErrorClass : ""}`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const checked = checkDevice(form);
    if ("errors" in checked) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});
    setServerError(null);
    setBusy(true);
    try {
      const created = await api.addDevice(locationId, checked.value);
      await onAdded(created);
      const range = suggestRange([...devices, created.device]);
      setForm({
        name: `Till ${devices.length + 2}`,
        start: String(range.start),
        end: String(range.end),
      });
    } catch (err) {
      setServerError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Add a till">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Name" htmlFor={`${uid}-name`} error={errors.name}>
            <input
              id={`${uid}-name`}
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              maxLength={200}
              className={cls("name")}
            />
          </Field>
          <Field label="First order number" htmlFor={`${uid}-start`} error={errors.start}>
            <input
              id={`${uid}-start`}
              value={form.start}
              onChange={(e) => setForm((f) => ({ ...f, start: e.target.value }))}
              inputMode="numeric"
              className={`${cls("start")} tabular-nums`}
            />
          </Field>
          <Field label="Last order number" htmlFor={`${uid}-end`} error={errors.end}>
            <input
              id={`${uid}-end`}
              value={form.end}
              onChange={(e) => setForm((f) => ({ ...f, end: e.target.value }))}
              inputMode="numeric"
              className={`${cls("end")} tabular-nums`}
            />
          </Field>
        </div>
        <p className="text-xs text-muted">
          Each till numbers its orders from its own range, so tills never clash, even offline.
          Ranges can&apos;t overlap.
        </p>
        {serverError && <ErrorNote>{serverError}</ErrorNote>}
        <button type="submit" disabled={busy} className={primaryButton}>
          {busy ? "Adding…" : "Add till and get a pairing code"}
        </button>
      </form>
    </Card>
  );
}
