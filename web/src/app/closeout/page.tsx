"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { friendlyError } from "@/lib/commands";
import { formatCents } from "@/lib/money";
import { providerLabel } from "@/lib/payments";
import type { Closeout } from "@/lib/types";
import { PairTill } from "@/components/PairTill";
import { REVOKED_NOTICE, useTill } from "@/components/TillProvider";

/** Today in the location's own timezone as yyyy-mm-dd: business dates are venue-local. */
function todayIn(timezone: string): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export default function CloseoutPage() {
  const { till, ready, unpair } = useTill();
  const [date, setDate] = useState<string | null>(null);
  const [report, setReport] = useState<Closeout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const token = till?.token;
  const locationId = till?.locationId;
  const timezone = till?.timezone ?? "Australia/Brisbane";

  useEffect(() => {
    if (till && date === null) setDate(todayIn(till.timezone));
  }, [till, date]);

  const load = useCallback(
    async (businessDate: string) => {
      if (!token || !locationId) return;
      setLoading(true);
      setError(null);
      try {
        setReport(await api.closeout(token, locationId, businessDate));
      } catch (err) {
        setReport(null);
        if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
        else setError(friendlyError(err));
      } finally {
        setLoading(false);
      }
    },
    [token, locationId, unpair],
  );

  useEffect(() => {
    if (date) load(date);
  }, [date, load]);

  if (!ready) return <p className="text-sm text-muted">Loading…</p>;
  if (!till) return <PairTill />;

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold">Shift closeout</h1>
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={date ?? ""}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-md border border-border bg-surface px-2 py-1.5 text-sm"
          />
          <button
            onClick={() => date && load(date)}
            className="rounded-md border border-border px-3 py-1.5 text-sm font-medium hover:bg-surface"
          >
            Refresh
          </button>
        </div>
      </div>

      {loading && <p className="text-sm text-muted">Loading…</p>}
      {error && (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
          {error}
        </p>
      )}

      {report && !loading && (
        <div className="space-y-4">
          <section className="rounded-2xl border border-border bg-surface p-4">
            <div className="flex items-baseline justify-between">
              <span className="text-sm text-muted">Gross sales</span>
              <span className="text-2xl font-semibold tabular-nums">
                {formatCents(report.gross_sales_cents)}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted">
              Money taken on business date {report.business_date}, part payments included
            </p>
          </section>

          <section className="rounded-2xl border border-border bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold">Paid orders and GST</h2>
            <dl className="space-y-1.5 text-sm">
              {(
                [
                  ["Sales (GST included)", report.paid_sales_cents],
                  ["GST", report.gst_cents],
                  ["Sales ex GST", report.sales_ex_gst_cents],
                  ["Still owing on open orders", report.open_balance_cents],
                ] as const
              ).map(([label, cents]) => (
                <div key={label} className="flex justify-between">
                  <dt className="text-muted">{label}</dt>
                  <dd className="tabular-nums">{formatCents(cents)}</dd>
                </div>
              ))}
            </dl>
          </section>

          <section className="rounded-2xl border border-border bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold">Orders</h2>
            <div className="grid grid-cols-5 gap-2 text-center">
              {(["total", "open", "sent", "paid", "voided"] as const).map((k) => (
                <div key={k} className="rounded-lg bg-background py-2">
                  <div className="text-lg font-semibold tabular-nums">{report.orders[k]}</div>
                  <div className="text-xs capitalize text-muted">{k}</div>
                </div>
              ))}
            </div>
          </section>

          <section className="rounded-2xl border border-border bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold">Payments by provider</h2>
            <Breakdown
              rows={report.payments_by_provider}
              label={providerLabel}
              emptyLabel="No captured payments."
            />
          </section>

          <section className="rounded-2xl border border-border bg-surface p-4">
            <h2 className="mb-3 text-sm font-semibold">Revenue by class</h2>
            <Breakdown rows={report.revenue_by_class_cents} emptyLabel="No paid sales." />
          </section>

          <p className="text-right text-xs text-muted">
            Generated{" "}
            {new Date(report.generated_at).toLocaleString("en-AU", { timeZone: timezone })} ·{" "}
            {timezone}
          </p>
        </div>
      )}
    </div>
  );
}

function Breakdown({
  rows,
  emptyLabel,
  label,
}: {
  rows: Record<string, number>;
  emptyLabel: string;
  /** Names rows for people; without it the raw name is shown capitalised. */
  label?: (name: string) => string;
}) {
  const entries = Object.entries(rows);
  if (entries.length === 0) {
    return <p className="text-sm text-muted">{emptyLabel}</p>;
  }
  return (
    <ul className="divide-y divide-border">
      {entries.map(([name, cents]) => (
        <li key={name} className="flex items-center justify-between py-2">
          <span className={label ? undefined : "capitalize"}>{label ? label(name) : name}</span>
          <span className="tabular-nums">{formatCents(cents)}</span>
        </li>
      ))}
    </ul>
  );
}
