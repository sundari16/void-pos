"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { config, isConfigured } from "@/lib/config";
import { formatCents } from "@/lib/money";
import { advanceOrderNumber, peekOrderNumber } from "@/lib/orderNumber";
import {
  draftItemFrom,
  orderLineCount,
  orderTotalCents,
  toLineIns,
  type DraftItem,
} from "@/lib/order";
import type { QuickModifier } from "@/lib/modifiers";
import type { KitchenResponse, MenuItem, OrderResponse, PaymentResponse } from "@/lib/types";
import { ConfigNotice } from "@/components/ConfigNotice";
import { ItemDialog } from "@/components/ItemDialog";
import { MenuGrid } from "@/components/MenuGrid";
import { OrderTicket } from "@/components/OrderTicket";

const FULFILLMENTS = ["dine_in", "pickup", "delivery"] as const;

interface Placed {
  order: OrderResponse;
  state: string;
  kitchen?: KitchenResponse;
  payment?: PaymentResponse;
}

function errMsg(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  return err instanceof Error ? err.message : String(err);
}

export default function PosPage() {
  const [items, setItems] = useState<MenuItem[]>([]);
  const [menuError, setMenuError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState<string>("dine_in");
  const [dialogItem, setDialogItem] = useState<MenuItem | null>(null);

  const [placed, setPlaced] = useState<Placed | null>(null);
  const [busy, setBusy] = useState<null | "place" | "kitchen" | "pay">(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [nextNumber, setNextNumber] = useState<number>(config.orderNumberStart);

  const configured = isConfigured();

  useEffect(() => {
    setNextNumber(peekOrderNumber(config.deviceId));
    if (!configured) {
      setLoading(false);
      return;
    }
    let active = true;
    api
      .listMenuItems(config.locationId)
      .then((list) => active && setItems(list))
      .catch((err) => active && setMenuError(errMsg(err)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [configured]);

  const editable = placed === null;
  const total = orderTotalCents(draft);
  const count = orderLineCount(draft);

  function addItem(item: MenuItem, quantity: number, modifiers: QuickModifier[]) {
    setDraft((prev) => [...prev, draftItemFrom(item, quantity, modifiers)]);
    setDialogItem(null);
  }

  function changeQty(ref: string, delta: number) {
    setDraft((prev) =>
      prev.map((it) =>
        it.ref === ref ? { ...it, quantity: Math.max(1, it.quantity + delta) } : it,
      ),
    );
  }

  function removeItem(ref: string) {
    setDraft((prev) => prev.filter((it) => it.ref !== ref));
  }

  async function placeOrder() {
    if (draft.length === 0) return;
    setBusy("place");
    setActionError(null);
    const orderNumber = peekOrderNumber(config.deviceId);
    try {
      const order = await api.placeOrder({
        device_id: config.deviceId,
        location_id: config.locationId,
        order_number: orderNumber,
        channel: "pos",
        fulfillment,
        lines: toLineIns(draft),
      });
      advanceOrderNumber(config.deviceId);
      setNextNumber(peekOrderNumber(config.deviceId));
      setPlaced({ order, state: order.state });
    } catch (err) {
      setActionError(errMsg(err));
    } finally {
      setBusy(null);
    }
  }

  async function sendToKitchen() {
    if (!placed) return;
    setBusy("kitchen");
    setActionError(null);
    try {
      const kitchen = await api.sendToKitchen(placed.order.id);
      setPlaced({ ...placed, state: kitchen.state, kitchen });
    } catch (err) {
      setActionError(errMsg(err));
    } finally {
      setBusy(null);
    }
  }

  async function payCash() {
    if (!placed) return;
    setBusy("pay");
    setActionError(null);
    try {
      const payment = await api.payOrder(placed.order.id, { method: "cash" });
      setPlaced({ ...placed, state: payment.order_state, payment });
    } catch (err) {
      setActionError(errMsg(err));
    } finally {
      setBusy(null);
    }
  }

  function newOrder() {
    setDraft([]);
    setPlaced(null);
    setActionError(null);
    setFulfillment("dine_in");
  }

  if (!configured) return <ConfigNotice />;

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      {/* Menu */}
      <section>
        {loading && <p className="text-sm text-muted">Loading menu…</p>}
        {menuError && (
          <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            Couldn&apos;t load the menu: {menuError}
          </p>
        )}
        {!loading && !menuError && <MenuGrid items={items} onPick={setDialogItem} />}
      </section>

      {/* Order ticket */}
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <div className="flex max-h-[calc(100dvh-6rem)] flex-col rounded-2xl border border-border bg-surface">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="font-semibold">
              Order <span className="text-muted">#{placed?.order.order_number ?? nextNumber}</span>
            </h2>
            {placed ? (
              <StateBadge state={placed.state} />
            ) : (
              <select
                value={fulfillment}
                onChange={(e) => setFulfillment(e.target.value)}
                className="rounded-md border border-border bg-background px-2 py-1 text-sm"
                aria-label="Fulfillment"
              >
                {FULFILLMENTS.map((f) => (
                  <option key={f} value={f}>
                    {f.replace("_", "-")}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-4">
            <OrderTicket
              items={draft}
              editable={editable}
              onQtyChange={changeQty}
              onRemove={removeItem}
            />
          </div>

          <div className="space-y-3 border-t border-border p-4">
            <div className="flex items-center justify-between text-sm text-muted">
              <span>{count} item{count === 1 ? "" : "s"}</span>
              <span className="text-lg font-semibold text-foreground tabular-nums">
                {formatCents(total)}
              </span>
            </div>

            {actionError && (
              <p className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
                {actionError}
              </p>
            )}

            {placed?.payment && (
              <p className="rounded-lg border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                Paid {formatCents(placed.payment.amount_cents)} · {placed.payment.provider} ·{" "}
                {placed.payment.status}
              </p>
            )}
            {placed?.kitchen && (
              <p className="text-xs text-muted">
                Sent to kitchen · {placed.kitchen.ticket_bytes} byte ticket
              </p>
            )}

            {!placed ? (
              <button
                onClick={placeOrder}
                disabled={draft.length === 0 || busy === "place"}
                className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
              >
                {busy === "place" ? "Placing…" : "Place order"}
              </button>
            ) : (
              <div className="space-y-2">
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={sendToKitchen}
                    disabled={placed.state !== "open" || busy !== null}
                    className="rounded-xl border border-border px-4 py-3 font-semibold disabled:opacity-40 hover:bg-background"
                  >
                    {busy === "kitchen" ? "Sending…" : placed.kitchen ? "Sent ✓" : "Send to kitchen"}
                  </button>
                  <button
                    onClick={payCash}
                    disabled={
                      (placed.state !== "open" && placed.state !== "sent") || busy !== null
                    }
                    className="rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
                  >
                    {busy === "pay" ? "Paying…" : placed.payment ? "Paid ✓" : "Pay cash"}
                  </button>
                </div>
                <button
                  onClick={newOrder}
                  className="w-full rounded-xl px-4 py-2 text-sm font-medium text-muted hover:bg-background"
                >
                  New order
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      {dialogItem && (
        <ItemDialog item={dialogItem} onAdd={addItem} onClose={() => setDialogItem(null)} />
      )}
    </div>
  );
}

function StateBadge({ state }: { state: string }) {
  const styles: Record<string, string> = {
    open: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
    sent: "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300",
    paid: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300",
    voided: "bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-300",
  };
  return (
    <span
      className={`rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${
        styles[state] ?? styles.open
      }`}
    >
      {state}
    </span>
  );
}
