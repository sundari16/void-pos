"use client";

import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { friendlyError, runCommand, type Pending } from "@/lib/commands";
import { formatCents } from "@/lib/money";
import {
  draftItemFrom,
  orderLineCount,
  orderTotalCents,
  toLineIns,
  type DraftItem,
} from "@/lib/order";
import type { QuickModifier } from "@/lib/modifiers";
import type {
  CommandIn,
  KitchenResponse,
  MenuItem,
  OrderResponse,
  PaymentResponse,
  PayRequest,
  PlaceOrderRequest,
} from "@/lib/types";
import { ulid } from "@/lib/ulid";
import { ItemDialog } from "@/components/ItemDialog";
import { MenuGrid } from "@/components/MenuGrid";
import { OrderTicket } from "@/components/OrderTicket";
import { PairTill } from "@/components/PairTill";
import { REVOKED_NOTICE, useTill } from "@/components/TillProvider";

const FULFILLMENTS = ["dine_in", "pickup", "delivery"] as const;

interface Placed {
  order: OrderResponse;
  state: string;
  kitchen?: KitchenResponse;
  payment?: PaymentResponse;
}

export default function PosPage() {
  const { till, ready, command, orderNumber, orderNumberAfter, seqAfter, unpair } = useTill();

  const [items, setItems] = useState<MenuItem[]>([]);
  const [menuError, setMenuError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState<string>("dine_in");
  const [dialogItem, setDialogItem] = useState<MenuItem | null>(null);

  const [placed, setPlaced] = useState<Placed | null>(null);
  const [busy, setBusy] = useState<null | "place" | "kitchen" | "pay">(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // A command's body (and so its ids) lives here until the server answers, so tapping
  // again after a dropped connection resends the same ids and can't duplicate anything.
  const pendingPlace = useRef<PlaceOrderRequest | null>(null);
  const pendingKitchen = useRef<CommandIn | null>(null);
  const pendingPay = useRef<PayRequest | null>(null);

  const token = till?.token;
  const locationId = till?.locationId;
  useEffect(() => {
    if (!token || !locationId) return;
    let active = true;
    setLoading(true);
    setMenuError(null);
    api
      .listMenuItems(token, locationId)
      .then((list) => active && setItems(list))
      .catch((err) => {
        if (!active) return;
        if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
        else setMenuError(friendlyError(err));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [token, locationId, unpair]);

  function send<B extends CommandIn, R>(
    pending: Pending<B>,
    build: () => B,
    call: (body: B) => Promise<R>,
    repair?: (err: ApiError, body: B) => B | null,
  ): Promise<R> {
    return runCommand({
      pending,
      build,
      send: call,
      seqAfter,
      onUnauthorized: () => unpair(REVOKED_NOTICE),
      repair,
    });
  }

  // While a placement is unconfirmed (the server may or may not have it), the ticket is
  // frozen: tapping Place order again must resend exactly this order, never a changed one.
  const unconfirmed = placed === null && pendingPlace.current !== null;
  const editable = placed === null && !unconfirmed;
  const total = orderTotalCents(draft);
  const count = orderLineCount(draft);

  function addItem(item: MenuItem, quantity: number, modifiers: QuickModifier[]) {
    if (!editable) return;
    setDraft((prev) => [...prev, draftItemFrom(item, quantity, modifiers)]);
    setDialogItem(null);
  }

  function changeQty(ref: string, delta: number) {
    if (!editable) return;
    setDraft((prev) =>
      prev.map((it) =>
        it.ref === ref ? { ...it, quantity: Math.max(1, it.quantity + delta) } : it,
      ),
    );
  }

  function removeItem(ref: string) {
    if (!editable) return;
    setDraft((prev) => prev.filter((it) => it.ref !== ref));
  }

  async function placeOrder() {
    if (!till || draft.length === 0) return;
    setBusy("place");
    setActionError(null);
    try {
      const order = await send(
        pendingPlace,
        (): PlaceOrderRequest => ({
          ...command(),
          order_id: ulid(),
          order_number: orderNumber(),
          channel: "pos",
          fulfillment,
          lines: toLineIns(draft),
        }),
        (body) => api.placeOrder(till.token, body),
        (err, body) => {
          const next = err.body.next_order_number;
          return err.code === "order_number_used" && typeof next === "number"
            ? { ...body, order_number: orderNumberAfter(next) }
            : null;
        },
      );
      setPlaced({ order, state: order.state });
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function sendToKitchen() {
    if (!till || !placed) return;
    setBusy("kitchen");
    setActionError(null);
    try {
      const kitchen = await send(pendingKitchen, command, (body) =>
        api.sendToKitchen(till.token, placed.order.id, body),
      );
      setPlaced({ ...placed, state: kitchen.state, kitchen });
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  async function payCash() {
    if (!till || !placed) return;
    setBusy("pay");
    setActionError(null);
    try {
      const payment = await send(
        pendingPay,
        (): PayRequest => ({ ...command(), payment_id: ulid(), method: "cash" }),
        (body) => api.payOrder(till.token, placed.order.id, body),
      );
      setPlaced({ ...placed, state: payment.order_state, payment });
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  function newOrder() {
    pendingPlace.current = null;
    pendingKitchen.current = null;
    pendingPay.current = null;
    setDraft([]);
    setPlaced(null);
    setActionError(null);
    setFulfillment("dine_in");
  }

  if (!ready) return <p className="text-sm text-muted">Loading…</p>;
  if (!till) return <PairTill />;

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
        {!loading && !menuError && <MenuGrid items={items} onPick={(item) => editable && setDialogItem(item)} />}
      </section>

      {/* Order ticket */}
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <div className="flex max-h-[calc(100dvh-6rem)] flex-col rounded-2xl border border-border bg-surface">
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <h2 className="font-semibold">
              Order{" "}
              <span className="text-muted">
                #
                {placed?.order.order_number ??
                  pendingPlace.current?.order_number ??
                  till.nextOrderNumber}
              </span>
            </h2>
            {placed ? (
              <StateBadge state={placed.state} />
            ) : (
              <select
                value={fulfillment}
                onChange={(e) => setFulfillment(e.target.value)}
                disabled={!editable}
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

            {unconfirmed && (
              <p className="text-xs text-muted">
                The order is held as it is until the server confirms it.
              </p>
            )}

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
                {placed.kitchen.printed
                  ? "Ticket printed in the kitchen."
                  : placed.kitchen.replayed
                    ? "Already sent to the kitchen."
                    : "Marked sent. No kitchen printer is set up on the server."}
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
