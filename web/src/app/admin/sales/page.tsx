"use client";

import { useCallback, useEffect, useState } from "react";
import { useAdminLocation } from "@/components/admin/AdminProvider";
import { OrderDialog, STATE_TONE } from "@/components/admin/OrderDialog";
import { Badge, Card, ErrorNote, PageHeader, smallButton } from "@/components/admin/ui";
import { errorDetail } from "@/lib/adminApi";
import { exportFileName, formatClock, formatDateTime, todayIn } from "@/lib/adminSession";
import type { Closeout, OrderState, OrderSummary } from "@/lib/adminTypes";
import { formatCents } from "@/lib/money";
import { providerLabel } from "@/lib/payments";

const STATES: OrderState[] = ["open", "sent", "paid", "voided"];

export default function SalesPage() {
  const { api, location } = useAdminLocation();
  const [date, setDate] = useState(() => todayIn(location.timezone));
  const [report, setReport] = useState<Closeout | null>(null);
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [filter, setFilter] = useState<OrderState | "all">("all");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [openOrder, setOpenOrder] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!date) return;
    setLoading(true);
    setError(null);
    try {
      const [closeout, list] = await Promise.all([
        api.closeout(location.id, date),
        api.orders(location.id, date, STATES),
      ]);
      setReport(closeout);
      setOrders(list);
    } catch (err) {
      setReport(null);
      setOrders(null);
      setError(errorDetail(err));
    } finally {
      setLoading(false);
    }
  }, [api, location.id, date]);

  useEffect(() => {
    load();
  }, [load]);

  async function downloadExport() {
    setExporting(true);
    setExportError(null);
    try {
      const blob = await api.exportBlob(location.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = exportFileName(location.name, todayIn(location.timezone));
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setExportError(errorDetail(err));
    } finally {
      setExporting(false);
    }
  }

  const shown = (orders ?? []).filter((o) => filter === "all" || o.state === filter);

  return (
    <div className="space-y-4">
      <PageHeader title="Sales">
        <label htmlFor="sales-date" className="sr-only">
          Business date
        </label>
        <input
          id="sales-date"
          type="date"
          value={date}
          onChange={(e) => setDate(e.target.value)}
          className="rounded-lg border border-border bg-surface px-2 py-1.5"
        />
        <button onClick={load} className={smallButton}>
          Refresh
        </button>
        <button onClick={downloadExport} disabled={exporting} className={smallButton}>
          {exporting ? "Preparing…" : "Download export"}
        </button>
      </PageHeader>

      {exportError && <ErrorNote>Export failed: {exportError}</ErrorNote>}
      {error && <ErrorNote>{error}</ErrorNote>}
      {loading && !report && <p className="text-sm text-muted">Loading…</p>}

      {report && (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <Stat label="Money taken" value={formatCents(report.gross_sales_cents)} note="Payments on the day, part payments included" />
            <Stat label="Paid sales" value={formatCents(report.paid_sales_cents)} note={`GST ${formatCents(report.gst_cents)} · ex GST ${formatCents(report.sales_ex_gst_cents)}`} />
            <Stat label="Still owing" value={formatCents(report.open_balance_cents)} note="On the day's open orders" />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Paid orders and GST">
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
            </Card>

            <Card title="Orders by state">
              <div className="grid grid-cols-5 gap-2 text-center">
                {(["total", "open", "sent", "paid", "voided"] as const).map((k) => (
                  <div key={k} className="rounded-lg bg-background py-2">
                    <div className="text-lg font-semibold tabular-nums">{report.orders[k]}</div>
                    <div className="text-xs capitalize text-muted">{k}</div>
                  </div>
                ))}
              </div>
            </Card>

            <Card title="Payments by provider">
              <Breakdown rows={report.payments_by_provider} label={providerLabel} empty="No captured payments." />
            </Card>

            <Card title="Revenue by class">
              <Breakdown rows={report.revenue_by_class_cents} empty="No paid sales." />
            </Card>
          </div>
        </>
      )}

      {orders && (
        <section className="rounded-2xl border border-border bg-surface">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-2.5">
            <h2 className="font-semibold">
              Orders on {date} <span className="text-sm font-normal text-muted">({orders.length})</span>
            </h2>
            <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by state">
              {(["all", ...STATES] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setFilter(s)}
                  aria-pressed={filter === s}
                  className={`rounded-lg px-2.5 py-1 text-sm font-medium capitalize ${
                    filter === s ? "bg-accent text-white" : "text-muted hover:bg-background"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
          {shown.length === 0 ? (
            <p className="p-4 text-muted">No orders{filter === "all" ? "" : ` ${filter}`} on this date.</p>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium">Order</th>
                  <th className="px-2 py-2 font-medium">Time</th>
                  <th className="px-2 py-2 font-medium">Table</th>
                  <th className="px-2 py-2 font-medium">State</th>
                  <th className="px-2 py-2 text-right font-medium">Total</th>
                  <th className="px-4 py-2 text-right font-medium">Owing</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {shown.map((o) => (
                  <tr
                    key={o.id}
                    onClick={() => setOpenOrder(o.id)}
                    className="cursor-pointer hover:bg-background"
                  >
                    <td className="px-4 py-2.5">
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenOrder(o.id);
                        }}
                        className="font-semibold text-accent hover:underline"
                      >
                        #{o.order_number}
                      </button>
                    </td>
                    <td className="px-2 py-2.5 text-muted" title={formatDateTime(o.created_at, location.timezone)}>
                      {formatClock(o.created_at, location.timezone)}
                    </td>
                    <td className="px-2 py-2.5">{o.table_label ?? <span className="text-muted">—</span>}</td>
                    <td className="px-2 py-2.5">
                      <Badge tone={STATE_TONE[o.state as OrderState] ?? "neutral"}>{o.state}</Badge>
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums">{formatCents(o.total_cents)}</td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      {o.state === "voided" || o.balance_cents === 0 ? (
                        <span className="text-muted">—</span>
                      ) : (
                        formatCents(o.balance_cents)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      )}

      {report && (
        <p className="text-right text-xs text-muted">
          Generated {formatDateTime(report.generated_at, location.timezone)} · {location.timezone}
        </p>
      )}

      {openOrder && (
        <OrderDialog
          api={api}
          orderId={openOrder}
          timezone={location.timezone}
          onClose={() => setOpenOrder(null)}
        />
      )}
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return (
    <div className="rounded-2xl border border-border bg-surface p-4">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-xs text-muted">{note}</p>
    </div>
  );
}

function Breakdown({
  rows,
  empty,
  label,
}: {
  rows: Record<string, number>;
  empty: string;
  label?: (name: string) => string;
}) {
  const entries = Object.entries(rows);
  if (entries.length === 0) return <p className="text-sm text-muted">{empty}</p>;
  return (
    <ul className="divide-y divide-border text-sm">
      {entries.map(([name, cents]) => (
        <li key={name} className="flex items-center justify-between py-2">
          <span className={label ? undefined : "capitalize"}>{label ? label(name) : name}</span>
          <span className="tabular-nums">{formatCents(cents)}</span>
        </li>
      ))}
    </ul>
  );
}
