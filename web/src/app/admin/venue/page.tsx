"use client";

import { useId, useState, type FormEvent } from "react";
import { useAdmin, useAdminLocation } from "@/components/admin/AdminProvider";
import {
  Card,
  ErrorNote,
  Field,
  inputClass,
  inputErrorClass,
  PageHeader,
  primaryButton,
  Success,
} from "@/components/admin/ui";
import { ApiError } from "@/lib/api";
import { errorDetail } from "@/lib/adminApi";
import type { AdminLocation, VenueDetailsIn } from "@/lib/adminTypes";

function formFrom(location: AdminLocation): VenueDetailsIn {
  return {
    business_name: location.business_name ?? "",
    abn: location.abn ?? "",
    address: location.address ?? "",
    phone: location.phone ?? "",
    receipt_footer: location.receipt_footer ?? "",
  };
}

export default function VenuePage() {
  const { api, location } = useAdminLocation();
  const { updateLocation } = useAdmin();
  const [form, setForm] = useState<VenueDetailsIn>(() => formFrom(location));
  const [surcharge, setSurcharge] = useState(() =>
    location.holiday_surcharge_bp ? String(location.holiday_surcharge_bp / 100) : "",
  );
  const [abnError, setAbnError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const uid = useId();

  const set = (key: keyof VenueDetailsIn, value: string) => {
    setSaved(false);
    if (key === "abn") setAbnError(null);
    setForm((f) => ({ ...f, [key]: value }));
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setAbnError(null);
    setSaved(false);
    const percent = surcharge.trim() === "" ? 0 : Number(surcharge);
    if (!Number.isFinite(percent) || percent < 0 || percent > 100) {
      setError("The holiday surcharge is a percentage from 0 to 100, e.g. 15.");
      setBusy(false);
      return;
    }
    try {
      const next = await api.updateVenue(location.id, {
        ...form,
        holiday_surcharge_bp: Math.round(percent * 100),
      });
      updateLocation(next);
      setForm(formFrom(next));
      setSaved(true);
    } catch (err) {
      if (err instanceof ApiError && err.code === "invalid_abn") setAbnError(err.detail);
      else setError(errorDetail(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <PageHeader title="Venue" />
      <p className="text-sm text-muted">
        {location.name} · {location.timezone}. What customer receipts show. With an ABN, receipts
        print as tax invoices; leave a field blank to clear it.
      </p>

      <Card>
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Field label="Business name" htmlFor={`${uid}-name`} hint="The name at the top of receipts.">
            <input
              id={`${uid}-name`}
              value={form.business_name}
              onChange={(e) => set("business_name", e.target.value)}
              maxLength={200}
              className={inputClass}
            />
          </Field>
          <Field
            label="ABN"
            htmlFor={`${uid}-abn`}
            error={abnError ?? undefined}
            hint="11 digits, spaces optional, e.g. 51 824 753 556."
          >
            <input
              id={`${uid}-abn`}
              value={form.abn}
              onChange={(e) => set("abn", e.target.value)}
              inputMode="numeric"
              maxLength={20}
              aria-invalid={abnError ? true : undefined}
              className={`${inputClass} font-mono tracking-wider ${abnError ? inputErrorClass : ""}`}
            />
          </Field>
          <Field label="Address" htmlFor={`${uid}-address`}>
            <textarea
              id={`${uid}-address`}
              value={form.address}
              onChange={(e) => set("address", e.target.value)}
              maxLength={300}
              rows={2}
              className={inputClass}
            />
          </Field>
          <Field label="Phone" htmlFor={`${uid}-phone`}>
            <input
              id={`${uid}-phone`}
              value={form.phone}
              onChange={(e) => set("phone", e.target.value)}
              type="tel"
              maxLength={40}
              className={inputClass}
            />
          </Field>
          <Field
            label="Receipt footer"
            htmlFor={`${uid}-footer`}
            hint="A line at the bottom of every receipt, e.g. Thanks for dining with us."
          >
            <textarea
              id={`${uid}-footer`}
              value={form.receipt_footer}
              onChange={(e) => set("receipt_footer", e.target.value)}
              maxLength={300}
              rows={2}
              className={inputClass}
            />
          </Field>

          <Field
            label="Public-holiday surcharge (%)"
            htmlFor={`${uid}-surcharge`}
            hint="The tills offer it as one button on a public holiday. Leave blank for none."
          >
            <input
              id={`${uid}-surcharge`}
              value={surcharge}
              onChange={(e) => {
                setSaved(false);
                setSurcharge(e.target.value);
              }}
              inputMode="decimal"
              placeholder="15"
              className={inputClass}
            />
          </Field>

          {abnError && <ErrorNote>Nothing was saved. Fix the ABN above and save again.</ErrorNote>}
          {error && <ErrorNote>{error}</ErrorNote>}
          {saved && <Success>Saved. New receipts use these details.</Success>}

          <button type="submit" disabled={busy} className={primaryButton}>
            {busy ? "Saving…" : "Save venue details"}
          </button>
        </form>
      </Card>
    </div>
  );
}
