"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { friendlyError, runCommand, type Pending } from "@/lib/commands";
import { groupsFor, localClock, type ChosenModifier } from "@/lib/menu";
import { formatCents } from "@/lib/money";
import { providerLabel, type PayMethod } from "@/lib/payments";
import {
  draftItemFrom,
  orderLineCount,
  orderTotalCents,
  toLineIns,
  type DraftItem,
} from "@/lib/order";
import type {
  CommandIn,
  KitchenResponse,
  OrderResponse,
  PaymentResponse,
  PayRequest,
  PlaceOrderRequest,
  TillMenu,
  TillMenuItem,
  VoidRequest,
  VoidResponse,
} from "@/lib/types";
import { ulid } from "@/lib/ulid";
import { ItemDialog } from "@/components/ItemDialog";
import { MenuGrid } from "@/components/MenuGrid";
import { OrderTicket } from "@/components/OrderTicket";
import { PairTill } from "@/components/PairTill";
import { PaymentPanel } from "@/components/PaymentPanel";
import { VoidPanel } from "@/components/VoidPanel";
import { REVOKED_NOTICE, useTill } from "@/components/TillProvider";

const FULFILLMENTS = ["dine_in", "pickup", "delivery"] as const;
const MENU_REFRESH_MS = 5 * 60_000;
const CLOCK_TICK_MS = 30_000;
// A refused order that means the till's menu is out of date: reload it.
const STALE_MENU = new Set([
  "item_unavailable",
  "modifier_unavailable",
  "modifier_not_allowed",
  "modifier_selection",
]);

interface Placed {
  order: OrderResponse;
  state: string;
  kitchen?: KitchenResponse;
  payments: PaymentResponse[];
  balance: number;
  voided?: VoidResponse;
}

export default function PosPage() {
  const { till, ready, command, orderNumber, orderNumberAfter, seqAfter, unpair } = useTill();

  const [menu, setMenu] = useState<TillMenu | null>(null);
  const [menuError, setMenuError] = useState<string | null>(null);
  const [now, setNow] = useState("00:00:00");

  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState<string>("dine_in");
  const [dialogItem, setDialogItem] = useState<TillMenuItem | null>(null);

  const [placed, setPlaced] = useState<Placed | null>(null);
  const [busy, setBusy] = useState<null | "place" | "kitchen" | "pay" | "void">(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // A command's body (and so its ids) lives here until the server answers, so tapping
  // again after a dropped connection resends the same ids and can't duplicate anything.
  const pendingPlace = useRef<PlaceOrderRequest | null>(null);
  const pendingKitchen = useRef<CommandIn | null>(null);
  const pendingPay = useRef<PayRequest | null>(null);
  const pendingVoid = useRef<VoidRequest | null>(null);

  const token = till?.token;
  const locationId = till?.locationId;
  const loadMenu = useCallback(async () => {
    if (!token || !locationId) return;
    try {
      const fresh = await api.tillMenu(token, locationId);
      setMenu(fresh);
      setNow(localClock(fresh.timezone));
      setMenuError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
      else setMenuError(friendlyError(err));
    }
  }, [token, locationId, unpair]);

  // Load the menu, then keep it fresh: an item switched off in admin greys out within
  // minutes, and the clock moves items in and out of their windows.
  useEffect(() => {
    void loadMenu();
    const refresh = setInterval(() => void loadMenu(), MENU_REFRESH_MS);
    return () => clearInterval(refresh);
  }, [loadMenu]);
  const timezone = menu?.timezone;
  useEffect(() => {
    if (!timezone) return;
    const tick = setInterval(() => setNow(localClock(timezone)), CLOCK_TICK_MS);
    return () => clearInterval(tick);
  }, [timezone]);

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

  function addItem(item: TillMenuItem, quantity: number, chosen: ChosenModifier[], note: string) {
    if (!editable) return;
    setDraft((prev) => [...prev, draftItemFrom(item, quantity, chosen, note)]);
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
      setPlaced({ order, state: order.state, payments: [], balance: order.balance_cents });
    } catch (err) {
      setActionError(friendlyError(err));
      if (err instanceof ApiError && STALE_MENU.has(err.code)) void loadMenu();
    } finally {
      setBusy(null);
    }
  }

  /** Apply a server answer to the order it was about, never to one started since. */
  function update(orderId: string, change: (p: Placed) => Placed) {
    setPlaced((current) => (current && current.order.id === orderId ? change(current) : current));
  }

  async function sendToKitchen() {
    if (!till || !placed) return;
    setBusy("kitchen");
    setActionError(null);
    try {
      const kitchen = await send(pendingKitchen, command, (body) =>
        api.sendToKitchen(till.token, placed.order.id, body),
      );
      update(placed.order.id, (p) => ({ ...p, state: kitchen.state, kitchen }));
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  /** Pay the order. An unconfirmed earlier attempt is always resent as it was, whichever
   *  button is pressed, so a dropped connection can't turn into a second charge. */
  async function pay(method: PayMethod, amountCents: number, externalRef?: string) {
    if (!till || !placed) return;
    setBusy("pay");
    setActionError(null);
    try {
      const payment = await send(
        pendingPay,
        (): PayRequest => ({
          ...command(),
          payment_id: ulid(),
          method,
          amount_cents: amountCents,
          external_ref: method === "external" ? externalRef : null,
        }),
        (body) => api.payOrder(till.token, placed.order.id, body),
      );
      update(placed.order.id, (p) => ({
        ...p,
        state: payment.order_state,
        payments: p.payments.some((x) => x.payment_id === payment.payment_id)
          ? p.payments
          : [...p.payments, payment],
        balance: payment.balance_cents,
      }));
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  /** Void the placed order. A retry resends the first attempt, reason and all. */
  async function voidOrder(reason: string) {
    if (!till || !placed) return;
    setBusy("void");
    setActionError(null);
    try {
      const voided = await send(
        pendingVoid,
        (): VoidRequest => ({ ...command(), reason }),
        (body) => api.voidOrder(till.token, placed.order.id, body),
      );
      update(placed.order.id, (p) => ({ ...p, state: voided.state, voided }));
    } catch (err) {
      setActionError(friendlyError(err));
    } finally {
      setBusy(null);
    }
  }

  function newOrder() {
    if (
      placed &&
      placed.payments.length > 0 &&
      placed.balance > 0 &&
      !window.confirm(
        `${formatCents(placed.balance)} is still owing on order #${placed.order.order_number}. ` +
          "Start a new order and leave it unpaid?",
      )
    ) {
      return;
    }
    pendingPlace.current = null;
    pendingKitchen.current = null;
    pendingPay.current = null;
    pendingVoid.current = null;
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
        {!menu && !menuError && <p className="text-sm text-muted">Loading menu…</p>}
        {menuError && (
          <p className="mb-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            Couldn&apos;t {menu ? "refresh" : "load"} the menu: {menuError}
          </p>
        )}
        {menu && (
          <MenuGrid
            items={menu.items}
            now={now}
            onPick={(item) => editable && setDialogItem(item)}
          />
        )}
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

            {placed?.voided && (
              <p className="rounded-lg border border-red-300 bg-red-50 p-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
                Voided.{" "}
                {placed.voided.printed
                  ? "A void ticket went to the kitchen."
                  : placed.kitchen && !placed.voided.replayed
                    ? "No void ticket printed: tell the kitchen."
                    : ""}
              </p>
            )}

            {placed && placed.payments.length > 0 && (
              <ul className="space-y-0.5 rounded-lg border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                {placed.payments.map((p) => (
                  <li key={p.payment_id}>
                    Paid {formatCents(p.amount_cents)} · {providerLabel(p.provider)}
                  </li>
                ))}
                <li className="font-semibold">
                  {placed.balance > 0 ? `${formatCents(placed.balance)} still owing` : "Paid in full"}
                </li>
              </ul>
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
                <button
                  onClick={sendToKitchen}
                  disabled={placed.state !== "open" || busy !== null}
                  className="w-full rounded-xl border border-border px-4 py-3 font-semibold disabled:opacity-40 hover:bg-background"
                >
                  {busy === "kitchen" ? "Sending…" : placed.kitchen ? "Sent ✓" : "Send to kitchen"}
                </button>

                {(placed.state === "open" || placed.state === "sent") && !pendingVoid.current && (
                  <PaymentPanel
                    balanceCents={placed.balance}
                    busy={busy === "pay"}
                    retryMethod={pendingPay.current?.method ?? null}
                    onPay={(method, amount, ref) => void pay(method, amount, ref)}
                  />
                )}
                {placed.payments.length === 0 &&
                  !pendingPay.current &&
                  (placed.state === "open" || placed.state === "sent") && (
                    <VoidPanel
                      busy={busy === "void"}
                      retrying={pendingVoid.current !== null}
                      onVoid={voidOrder}
                    />
                  )}
                <button
                  onClick={newOrder}
                  disabled={busy !== null}
                  className="w-full rounded-xl px-4 py-2 text-sm font-medium text-muted hover:bg-background disabled:opacity-40"
                >
                  New order
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      {dialogItem && menu && (
        <ItemDialog
          item={dialogItem}
          groups={groupsFor(dialogItem, menu)}
          now={now}
          onAdd={addItem}
          onClose={() => setDialogItem(null)}
        />
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
