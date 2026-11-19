"use client";

import { useCallback, useEffect, useState } from "react";
import { ReceiptDialog } from "@/components/ReceiptDialog";
import { errorDetail, type AdminApi } from "@/lib/adminApi";
import { lineRows } from "@/lib/adminMenu";
import { formatDateTime } from "@/lib/adminSession";
import type { OrderResponse } from "@/lib/adminTypes";
import { formatCents } from "@/lib/money";
import { providerLabel } from "@/lib/payments";
import { Badge, Dialog, ErrorNote, primaryButton, secondaryButton } from "./ui";

export const STATE_TONE = {
  open: "warn",
  sent: "accent",
  paid: "good",
  voided: "bad",
} as const;

/** One order as the server has it: lines with their modifiers, payments, and its receipt. */
export function OrderDialog({
  api,
  orderId,
  timezone,
  onClose,
}: {
  api: AdminApi;
  orderId: string;
  timezone: string;
  onClose: () => void;
}) {
  const [order, setOrder] = useState<OrderResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showReceipt, setShowReceipt] = useState(false);

  useEffect(() => {
    let active = true;
    api
      .order(orderId)
      .then((o) => active && setOrder(o))
      .catch((err) => active && setError(errorDetail(err)));
    return () => {
      active = false;
    };
  }, [api, orderId]);

  const loadReceipt = useCallback(() => api.receipt(orderId), [api, orderId]);
  const printReceipt = useCallback(() => api.printReceipt(orderId), [api, orderId]);

  if (showReceipt) {
    return <ReceiptDialog load={loadReceipt} print={printReceipt} onClose={() => setShowReceipt(false)} />;
  }

  return (
    <Dialog title={order ? `Order #${order.order_number}` : "Order"} onClose={onClose}>
      {error && <ErrorNote>{error}</ErrorNote>}
      {!order && !error && <p className="text-sm text-muted">Loading…</p>}
      {order && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={STATE_TONE[order.state as keyof typeof STATE_TONE] ?? "neutral"}>
              {order.state}
            </Badge>
            <span className="capitalize text-muted">{order.fulfillment.replace(/_/g, " ")}</span>
            {order.table_label && <span className="text-muted">· Table {order.table_label}</span>}
            <span className="text-muted">· {formatDateTime(order.created_at, timezone)}</span>
          </div>

          <ul className="divide-y divide-border rounded-xl border border-border">
            {lineRows(order.lines).map((row) => (
              <li
                key={row.id}
                className={`flex justify-between gap-3 px-3 py-2 ${row.isModifier ? "pl-8 text-sm text-muted" : ""}`}
              >
                <span>
                  {row.isModifier ? "+ " : ""}
                  {row.quantity > 1 || !row.isModifier ? `${row.quantity}× ` : ""}
                  {row.name}
                </span>
                <span className="tabular-nums">
                  {row.isModifier && row.amountCents === 0 ? "" : formatCents(row.amountCents)}
                </span>
              </li>
            ))}
          </ul>

          <dl className="space-y-1 text-sm">
            <Row label="Total" value={formatCents(order.total_cents)} bold />
            <Row label="Includes GST" value={formatCents(order.gst_cents)} />
            {order.payments.map((p) => (
              <Row
                key={p.payment_id}
                label={`${providerLabel(p.provider)}${p.external_ref ? ` · ref ${p.external_ref}` : ""}${
                  p.status === "captured" ? "" : ` (${p.status})`
                }`}
                value={formatCents(p.amount_cents)}
              />
            ))}
            {order.state !== "voided" && (
              <Row label="Still owing" value={formatCents(order.balance_cents)} bold={order.balance_cents > 0} />
            )}
          </dl>

          <div className="flex flex-wrap gap-2">
            <button onClick={() => setShowReceipt(true)} className={primaryButton}>
              Receipt
            </button>
            <button onClick={onClose} className={secondaryButton}>
              Close
            </button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Row({ label, value, bold = false }: { label: string; value: string; bold?: boolean }) {
  return (
    <div className={`flex justify-between gap-3 ${bold ? "font-semibold" : ""}`}>
      <dt className={bold ? "" : "text-muted"}>{label}</dt>
      <dd className="tabular-nums">{value}</dd>
    </div>
  );
}
