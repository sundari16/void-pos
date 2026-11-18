"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { friendlyError } from "@/lib/commands";
import { formatCents } from "@/lib/money";
import type { ReceiptOut } from "@/lib/types";

/** The customer receipt on screen, to print on the receipt printer or from this device. */
export function ReceiptDialog({
  load,
  print,
  onClose,
}: {
  load: () => Promise<ReceiptOut>;
  print: () => Promise<unknown>;
  onClose: () => void;
}) {
  const [receipt, setReceipt] = useState<ReceiptOut | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [printing, setPrinting] = useState(false);

  useEffect(() => {
    let active = true;
    load()
      .then((r) => active && setReceipt(r))
      .catch((err) => active && setMessage(friendlyError(err)));
    return () => {
      active = false;
    };
  }, [load]);

  async function toPrinter() {
    setPrinting(true);
    setMessage(null);
    try {
      await print();
      setMessage("Sent to the receipt printer.");
    } catch (err) {
      setMessage(
        err instanceof ApiError && err.code === "no_receipt_printer"
          ? "No receipt printer is set up. Use Print from this device."
          : friendlyError(err),
      );
    } finally {
      setPrinting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 grid place-items-center bg-black/40 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-label="Receipt"
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[90dvh] w-full max-w-sm flex-col overflow-hidden rounded-2xl bg-surface shadow-xl"
      >
        <div className="flex-1 overflow-y-auto p-4">
          {!receipt && !message && <p className="text-sm text-muted">Loading…</p>}
          {receipt && <Paper receipt={receipt} />}
        </div>
        <div className="space-y-2 border-t border-border p-4 print:hidden">
          {message && <p className="text-sm text-muted">{message}</p>}
          <div className="grid grid-cols-2 gap-2">
            <button
              onClick={toPrinter}
              disabled={!receipt || printing}
              className="rounded-xl bg-accent px-3 py-2.5 font-semibold text-white disabled:opacity-40"
            >
              {printing ? "Printing…" : "Receipt printer"}
            </button>
            <button
              onClick={() => window.print()}
              disabled={!receipt}
              className="rounded-xl border border-border px-3 py-2.5 font-medium hover:bg-background disabled:opacity-40"
            >
              Print from this device
            </button>
          </div>
          <button
            onClick={onClose}
            className="w-full rounded-xl px-3 py-2 text-sm text-muted hover:bg-background"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function Paper({ receipt }: { receipt: ReceiptOut }) {
  return (
    <div className="receipt-print mx-auto max-w-[20rem] font-mono text-sm leading-snug text-black">
      <div className="rounded-md bg-white p-4 text-center shadow-inner">
        <p className="font-bold">{receipt.venue_name}</p>
        {receipt.address && <p>{receipt.address}</p>}
        {receipt.phone && <p>{receipt.phone}</p>}
        {receipt.abn && <p>ABN {receipt.abn}</p>}
        <p className="mt-2 font-bold">{receipt.title}</p>
        {receipt.voided && <p className="text-lg font-bold">VOID</p>}
        <div className="mt-2 text-left">
          <p>
            Order #{receipt.order_number}
            {receipt.table_label && `  Table ${receipt.table_label}`}
          </p>
          <p>{receipt.ordered_at}</p>
          <hr className="my-2 border-dashed border-black" />
          {receipt.lines.map((line, i) => (
            <Row
              key={i}
              left={
                line.is_modifier
                  ? `  + ${line.quantity > 1 ? `${line.quantity}x ` : ""}${line.name}`
                  : `${line.quantity}x ${line.name}`
              }
              right={line.is_modifier && line.amount_cents === 0 ? "" : formatCents(line.amount_cents)}
            />
          ))}
          <hr className="my-2 border-dashed border-black" />
          <Row left="TOTAL" right={formatCents(receipt.total_cents)} bold />
          <Row left="Includes GST" right={formatCents(receipt.gst_cents)} />
          {receipt.payments.map((p, i) => (
            <div key={i}>
              <Row left={p.label} right={formatCents(p.amount_cents)} />
              {p.reference && <p className="pl-4 text-xs">Ref {p.reference}</p>}
            </div>
          ))}
          {receipt.balance_cents > 0 && !receipt.voided && (
            <Row left="Still owing" right={formatCents(receipt.balance_cents)} bold />
          )}
        </div>
        {receipt.footer && <p className="mt-3">{receipt.footer}</p>}
        <p className="mt-2 text-xs">Printed {receipt.printed_at}</p>
      </div>
    </div>
  );
}

function Row({ left, right, bold = false }: { left: string; right: string; bold?: boolean }) {
  return (
    <p className={`flex justify-between gap-3 whitespace-pre-wrap ${bold ? "font-bold" : ""}`}>
      <span>{left}</span>
      <span className="tabular-nums">{right}</span>
    </p>
  );
}
