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
import {
  isUnsettled,
  refundable,
  sendLabel,
  withKitchen,
  withPayment,
  withVoid,
} from "@/lib/placed";
import type {
  AddLinesRequest,
  AdjustRequest,
  CommandIn,
  KitchenResponse,
  OrderLineOut,
  OrderResponse,
  PayRequest,
  PlaceOrderRequest,
  RefundRequest,
  RemoveLinesRequest,
  TillMenu,
  TillMenuItem,
  VoidRequest,
  VoidResponse,
} from "@/lib/types";
import { ulid } from "@/lib/ulid";
import { AdjustPanel, type NewAdjustment } from "@/components/AdjustPanel";
import { ItemDialog } from "@/components/ItemDialog";
import { MenuGrid } from "@/components/MenuGrid";
import { OpenOrders } from "@/components/OpenOrders";
import { OrderTicket } from "@/components/OrderTicket";
import { PairTill } from "@/components/PairTill";
import { PaymentPanel } from "@/components/PaymentPanel";
import { PinPrompt } from "@/components/PinPrompt";
import { PlacedLines } from "@/components/PlacedLines";
import { ReceiptDialog } from "@/components/ReceiptDialog";
import { RefundDialog, type RefundChoice } from "@/components/RefundDialog";
import { RemoveItemDialog } from "@/components/RemoveItemDialog";
import { StripeCardDialog } from "@/components/StripeCardDialog";
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
const NEEDS_PIN = new Set(["manager_pin_required", "wrong_pin"]);

interface Placed {
  order: OrderResponse;
  kitchen?: KitchenResponse;
  voided?: VoidResponse;
}

interface PinAsk {
  title: string;
  error?: string;
  resolve: (pin: string | null) => void;
}

type Busy =
  | null
  | "place"
  | "add"
  | "kitchen"
  | "pay"
  | "void"
  | "open"
  | "menu"
  | "remove"
  | "adjust"
  | "refund";

export default function PosPage() {
  const { till, ready, command, orderNumber, orderNumberAfter, seqAfter, unpair } = useTill();

  const [menu, setMenu] = useState<TillMenu | null>(null);
  const [menuError, setMenuError] = useState<string | null>(null);
  const [now, setNow] = useState("00:00:00");
  const [soldOutMode, setSoldOutMode] = useState(false);

  // The new order being built, or the items being added to a placed one.
  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState<string>("dine_in");
  const [table, setTable] = useState("");
  const [dialogItem, setDialogItem] = useState<TillMenuItem | null>(null);

  const [placed, setPlaced] = useState<Placed | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showOpen, setShowOpen] = useState(false);
  const [showReceipt, setShowReceipt] = useState(false);
  const [pinAsk, setPinAsk] = useState<PinAsk | null>(null);
  const [removing, setRemoving] = useState<OrderLineOut | null>(null);
  const [showRefund, setShowRefund] = useState(false);
  const [stripeOn, setStripeOn] = useState(false);
  // A card payment through Stripe in progress: its payment id is fixed so the intent and
  // the recorded payment match, and a retry reuses both.
  const [stripeCheckout, setStripeCheckout] = useState<{
    paymentId: string;
    amount: number;
    tip: number;
  } | null>(null);

  // A command's body (and so its ids) lives here until the server answers, so tapping
  // again after a dropped connection resends the same ids and can't duplicate anything.
  const pendingPlace = useRef<PlaceOrderRequest | null>(null);
  const pendingAdd = useRef<AddLinesRequest | null>(null);
  const pendingKitchen = useRef<CommandIn | null>(null);
  const pendingPay = useRef<PayRequest | null>(null);
  const pendingVoid = useRef<VoidRequest | null>(null);
  const pendingRemove = useRef<RemoveLinesRequest | null>(null);
  const pendingAdjust = useRef<AdjustRequest | null>(null);
  const pendingUnadjust = useRef<CommandIn | null>(null);
  const pendingRefund = useRef<RefundRequest | null>(null);

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

  // Load the menu, then keep it fresh: an item switched off greys out within minutes,
  // and the clock moves items in and out of their windows.
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

  useEffect(() => {
    if (!token) return;
    api
      .stripeConfig(token)
      .then((c) => setStripeOn(c.enabled))
      .catch(() => setStripeOn(false));
  }, [token]);

  const loadOpenOrders = useCallback(
    () => (token && locationId ? api.openOrders(token, locationId) : Promise.resolve([])),
    [token, locationId],
  );

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

  function askPin(title: string, error?: string): Promise<string | null> {
    return new Promise((resolve) => setPinAsk({ title, error, resolve }));
  }

  /** Try an action; if it needs a manager's approval, ask for the PIN and try again.
   *  Resolves to null if the manager cancels. */
  async function withApproval<T>(title: string, attempt: (pin?: string) => Promise<T>) {
    let pin: string | undefined;
    for (;;) {
      try {
        return await attempt(pin);
      } catch (err) {
        if (!(err instanceof ApiError) || !NEEDS_PIN.has(err.code)) throw err;
        const next = await askPin(
          title,
          err.code === "wrong_pin" && pin ? "That PIN isn't a manager's." : undefined,
        );
        if (next === null) return null;
        pin = next;
      }
    }
  }

  const order = placed?.order ?? null;
  // While a placement or addition is unconfirmed (the server may or may not have it), the
  // draft is frozen: tapping again must resend exactly what was sent, never a changed one.
  const unconfirmed =
    (order === null && pendingPlace.current !== null) || pendingAdd.current !== null;
  const canAdd =
    order !== null &&
    isUnsettled(order) &&
    pendingVoid.current === null &&
    pendingKitchen.current === null;
  const editable = !unconfirmed && (order === null || canAdd);
  const draftTotal = orderTotalCents(draft);
  const draftCount = orderLineCount(draft);

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

  function failed(err: unknown) {
    setActionError(friendlyError(err));
    if (err instanceof ApiError && STALE_MENU.has(err.code)) void loadMenu();
  }

  /** Apply a server answer to the order it was about, never to one opened since. */
  function update(orderId: string, change: (p: Placed) => Placed) {
    setPlaced((current) => (current && current.order.id === orderId ? change(current) : current));
  }

  async function placeOrder() {
    if (!till || draft.length === 0) return;
    setBusy("place");
    setActionError(null);
    try {
      const placedOrder = await send(
        pendingPlace,
        (): PlaceOrderRequest => ({
          ...command(),
          order_id: ulid(),
          order_number: orderNumber(),
          channel: "pos",
          fulfillment,
          table_label: fulfillment === "dine_in" && table.trim() ? table.trim() : null,
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
      setPlaced({ order: placedOrder });
      setDraft([]);
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function addToOrder() {
    if (!till || !order || draft.length === 0) return;
    setBusy("add");
    setActionError(null);
    try {
      const updated = await send(
        pendingAdd,
        (): AddLinesRequest => ({ ...command(), lines: toLineIns(draft) }),
        (body) => api.addLines(till.token, order.id, body),
      );
      update(order.id, (p) => ({ ...p, order: updated }));
      setDraft([]);
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function sendToKitchen() {
    if (!till || !order) return;
    setBusy("kitchen");
    setActionError(null);
    try {
      const kitchen = await send(pendingKitchen, command, (body) =>
        api.sendToKitchen(till.token, order.id, body),
      );
      // A replay doesn't say which lines that earlier send covered: ask the server.
      const fresh = kitchen.replayed
        ? await api.getOrder(till.token, order.id).catch(() => null)
        : null;
      update(order.id, (p) => ({
        ...p,
        order: fresh ?? withKitchen(p.order, kitchen),
        kitchen,
      }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  /** Pay the order. An unconfirmed earlier attempt is always resent as it was, whichever
   *  button is pressed, so a dropped connection can't turn into a second charge. */
  async function pay(
    method: PayMethod,
    amountCents: number,
    externalRef?: string,
    tipCents = 0,
    stripe?: { paymentId: string; intentId: string },
  ) {
    if (!till || !order) return;
    setBusy("pay");
    setActionError(null);
    try {
      const payment = await send(
        pendingPay,
        (): PayRequest => ({
          ...command(),
          payment_id: stripe?.paymentId ?? ulid(),
          method,
          amount_cents: amountCents,
          tip_cents: tipCents,
          external_ref: method === "external" ? externalRef : null,
          stripe_payment_intent_id: stripe?.intentId ?? null,
        }),
        (body) => api.payOrder(till.token, order.id, body),
      );
      update(order.id, (p) => ({ ...p, order: withPayment(p.order, payment) }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  /** Void the placed order, with a manager's PIN once the venue has a manager. A retry
   *  after a dropped connection resends the first attempt, reason and all. */
  async function voidOrder(reason: string) {
    if (!till || !order) return;
    setBusy("void");
    setActionError(null);
    try {
      const voided = await withApproval(`Void order #${order.order_number}`, (pin) =>
        send(
          pendingVoid,
          (): VoidRequest => ({ ...command(), reason, manager_pin: pin ?? null }),
          (body) => api.voidOrder(till.token, order.id, body),
        ),
      );
      if (voided) update(order.id, (p) => ({ ...p, order: withVoid(p.order, voided), voided }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  /** Take an item off: a manager's PIN if the kitchen has it (and the venue has managers). */
  async function removeLine(line: OrderLineOut, reason: string) {
    if (!till || !order) return;
    setBusy("remove");
    setActionError(null);
    try {
      const updated = await withApproval(`Take off ${line.name}`, (pin) =>
        send(
          pendingRemove,
          (): RemoveLinesRequest => ({
            ...command(),
            line_ids: [line.id],
            reason,
            manager_pin: pin ?? null,
          }),
          (body) => api.removeLines(till.token, order.id, body),
        ),
      );
      if (updated) update(order.id, (p) => ({ ...p, order: updated }));
      setRemoving(null);
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function addAdjustment(adj: NewAdjustment) {
    if (!till || !order) return;
    setBusy("adjust");
    setActionError(null);
    const title = adj.kind === "discount" ? "Approve the discount" : "Add the surcharge";
    try {
      const updated = await withApproval(title, (pin) =>
        send(
          pendingAdjust,
          (): AdjustRequest => ({ ...command(), id: ulid(), ...adj, manager_pin: pin ?? null }),
          (body) => api.adjust(till.token, order.id, body),
        ),
      );
      if (updated) update(order.id, (p) => ({ ...p, order: updated }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function takeOffAdjustment(adjustmentId: string) {
    if (!till || !order) return;
    setBusy("adjust");
    setActionError(null);
    try {
      const updated = await send(pendingUnadjust, command, (body) =>
        api.removeAdjustment(till.token, order.id, adjustmentId, body),
      );
      update(order.id, (p) => ({ ...p, order: updated }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function refund(choice: RefundChoice) {
    if (!till || !order) return;
    setBusy("refund");
    setActionError(null);
    try {
      const updated = await withApproval(`Refund order #${order.order_number}`, (pin) =>
        send(
          pendingRefund,
          (): RefundRequest => ({
            ...command(),
            refund_id: ulid(),
            payment_id: choice.paymentId,
            amount_cents: choice.amountCents,
            reason: choice.reason,
            external_ref: choice.externalRef,
            manager_pin: pin ?? null,
          }),
          (body) => api.refund(till.token, order.id, body),
        ),
      );
      if (updated) {
        update(order.id, (p) => ({ ...p, order: updated }));
        setShowRefund(false);
      }
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  async function toggleSoldOut(item: TillMenuItem) {
    if (!till) return;
    setBusy("menu");
    setActionError(null);
    const title = item.is_available ? `Mark ${item.name} sold out` : `Put ${item.name} back on`;
    try {
      const done = await withApproval(title, (pin) =>
        api.setAvailability(till.token, item.id, {
          is_available: !item.is_available,
          manager_pin: pin ?? null,
        }),
      );
      if (done) await loadMenu();
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  function leaveCurrent(): boolean {
    if (order === null && draft.length > 0) {
      return window.confirm("Discard the order you're building?");
    }
    if (order && draft.length > 0) {
      return window.confirm(`Discard the items not yet added to order #${order.order_number}?`);
    }
    if (order && order.payments.length > 0 && order.balance_cents > 0) {
      return window.confirm(
        `${formatCents(order.balance_cents)} is still owing on order #${order.order_number}. ` +
          "It stays in Open orders. Leave it for now?",
      );
    }
    return true;
  }

  function reset(next: Placed | null) {
    pendingPlace.current = null;
    pendingAdd.current = null;
    pendingKitchen.current = null;
    pendingPay.current = null;
    pendingVoid.current = null;
    pendingRemove.current = null;
    pendingAdjust.current = null;
    pendingUnadjust.current = null;
    pendingRefund.current = null;
    setRemoving(null);
    setShowRefund(false);
    setDraft([]);
    setPlaced(next);
    setActionError(null);
    setFulfillment("dine_in");
    setTable("");
  }

  function newOrder() {
    if (leaveCurrent()) reset(null);
  }

  async function openOrder(id: string) {
    if (!till || !leaveCurrent()) return;
    setShowOpen(false);
    setBusy("open");
    try {
      reset({ order: await api.getOrder(till.token, id) });
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
    }
  }

  if (!ready) return <p className="text-sm text-muted">Loading…</p>;
  if (!till) return <PairTill />;

  const pickItem = (item: TillMenuItem) => {
    if (busy !== null || pinAsk !== null) return;
    if (soldOutMode) void toggleSoldOut(item);
    else if (editable) setDialogItem(item);
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      {/* Menu */}
      <section>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            onClick={() => setShowOpen(true)}
            disabled={busy !== null}
            className="rounded-xl border border-border bg-surface px-3 py-2 text-sm font-semibold hover:border-accent disabled:opacity-40"
          >
            Open orders
          </button>
          <button
            onClick={() => setSoldOutMode((on) => !on)}
            aria-pressed={soldOutMode}
            className={`rounded-xl border px-3 py-2 text-sm font-semibold ${
              soldOutMode
                ? "border-red-600 bg-red-600 text-white"
                : "border-border bg-surface hover:border-accent"
            }`}
          >
            {soldOutMode ? "Done marking sold out" : "Mark sold out"}
          </button>
          {soldOutMode && (
            <span className="text-sm text-muted">Tap a dish to switch it off or back on.</span>
          )}
        </div>
        {!menu && !menuError && <p className="text-sm text-muted">Loading menu…</p>}
        {menuError && (
          <p className="mb-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300">
            Couldn&apos;t {menu ? "refresh" : "load"} the menu: {menuError}
          </p>
        )}
        {menu && <MenuGrid items={menu.items} now={now} onPick={pickItem} manage={soldOutMode} />}
      </section>

      {/* Order ticket */}
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <div className="flex max-h-[calc(100dvh-6rem)] flex-col rounded-2xl border border-border bg-surface">
          <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
            <h2 className="font-semibold">
              {order?.table_label ? `Table ${order.table_label} ` : "Order "}
              <span className="text-muted">
                #
                {order?.order_number ?? pendingPlace.current?.order_number ?? till.nextOrderNumber}
              </span>
            </h2>
            {order ? (
              <StateBadge state={order.state} />
            ) : (
              <div className="flex items-center gap-2">
                {fulfillment === "dine_in" && (
                  <input
                    value={table}
                    onChange={(e) => setTable(e.target.value.slice(0, 40))}
                    disabled={!editable}
                    placeholder="Table"
                    aria-label="Table"
                    className="w-20 rounded-md border border-border bg-background px-2 py-1 text-sm"
                  />
                )}
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
              </div>
            )}
          </div>

          <div className="flex-1 overflow-y-auto px-4">
            {order && (
              <PlacedLines
                lines={order.lines}
                onRemove={canAdd && draft.length === 0 && busy === null ? setRemoving : undefined}
              />
            )}
            {order && draft.length > 0 && (
              <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-muted">
                Adding
              </p>
            )}
            {(order === null || draft.length > 0) && (
              <OrderTicket
                items={draft}
                editable={editable}
                onQtyChange={changeQty}
                onRemove={removeItem}
              />
            )}
          </div>

          <div className="space-y-3 border-t border-border p-4">
            {order ? (
              <div className="flex items-center justify-between text-sm text-muted">
                <span>
                  Total {formatCents(order.total_cents)}
                  {draft.length > 0 && ` · adding ${formatCents(draftTotal)}`}
                </span>
                <span className="text-lg font-semibold text-foreground tabular-nums">
                  {order.balance_cents > 0 || isUnsettled(order)
                    ? `${formatCents(order.balance_cents)} owing`
                    : formatCents(order.total_cents)}
                </span>
              </div>
            ) : (
              <div className="flex items-center justify-between text-sm text-muted">
                <span>
                  {draftCount} item{draftCount === 1 ? "" : "s"}
                </span>
                <span className="text-lg font-semibold text-foreground tabular-nums">
                  {formatCents(draftTotal)}
                </span>
              </div>
            )}

            {order && isUnsettled(order) && draft.length === 0 && (
              <AdjustPanel
                adjustments={order.adjustments}
                holidaySurchargeBp={till.holidaySurchargeBp}
                busy={busy !== null}
                onAdd={(adj) => void addAdjustment(adj)}
                onRemove={(id) => void takeOffAdjustment(id)}
              />
            )}
            {order && !isUnsettled(order) && order.adjustments.length > 0 && (
              <ul className="space-y-0.5 text-sm text-muted">
                {order.adjustments.map((a) => (
                  <li key={a.id} className="flex justify-between">
                    <span>{a.label ?? (a.kind === "discount" ? "Discount" : "Surcharge")}</span>
                    <span className="tabular-nums">{formatCents(a.amount_cents)}</span>
                  </li>
                ))}
              </ul>
            )}

            {unconfirmed && (
              <p className="text-xs text-muted">
                Held as it is until the server confirms it; tap again to retry.
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
                  : order?.lines.some((l) => l.sent) && !placed.voided.replayed
                    ? "No void ticket printed: tell the kitchen."
                    : ""}
              </p>
            )}

            {order && order.payments.length > 0 && (
              <ul className="space-y-0.5 rounded-lg border border-emerald-300 bg-emerald-50 p-2 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300">
                {order.payments.map((p) => (
                  <li key={p.payment_id}>
                    Paid {formatCents(p.amount_cents)} · {providerLabel(p.provider)}
                    {p.tip_cents > 0 && ` · tip ${formatCents(p.tip_cents)}`}
                  </li>
                ))}
                {order.refunds.map((r) => (
                  <li key={r.refund_id} className="text-red-700 dark:text-red-300">
                    Refunded {formatCents(r.amount_cents)} · {providerLabel(r.provider)} ·{" "}
                    {r.reason}
                  </li>
                ))}
                <li className="font-semibold">
                  {order.state === "refunded"
                    ? "Refunded in full"
                    : order.balance_cents > 0
                      ? `${formatCents(order.balance_cents)} still owing`
                      : "Paid in full"}
                </li>
              </ul>
            )}
            {placed?.kitchen && (
              <p className="text-xs text-muted">
                {placed.kitchen.replayed
                  ? "Already sent to the kitchen."
                  : placed.kitchen.printed
                    ? placed.kitchen.lines_sent
                      ? "Ticket printed in the kitchen."
                      : "Kitchen ticket reprinted."
                    : "Marked sent. No kitchen printer is set up on the server."}
              </p>
            )}

            {!order ? (
              <button
                onClick={placeOrder}
                disabled={draft.length === 0 || busy === "place"}
                className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
              >
                {busy === "place" ? "Placing…" : "Place order"}
              </button>
            ) : (
              <div className="space-y-2">
                {draft.length > 0 && (
                  <button
                    onClick={addToOrder}
                    disabled={busy !== null}
                    className="w-full rounded-xl bg-accent px-4 py-3 font-semibold text-white disabled:opacity-40"
                  >
                    {busy === "add"
                      ? "Adding…"
                      : `Add ${draftCount} item${draftCount === 1 ? "" : "s"} to the order`}
                  </button>
                )}
                {order.state !== "voided" && (
                  <button
                    onClick={sendToKitchen}
                    disabled={busy !== null || draft.length > 0}
                    className={`w-full rounded-xl border px-4 py-3 font-semibold disabled:opacity-40 ${
                      order.unsent_lines > 0
                        ? "border-accent text-accent"
                        : "border-border hover:bg-background"
                    }`}
                  >
                    {busy === "kitchen" ? "Sending…" : sendLabel(order)}
                  </button>
                )}

                {isUnsettled(order) && !pendingVoid.current && draft.length === 0 && (
                  <PaymentPanel
                    balanceCents={order.balance_cents}
                    busy={busy === "pay"}
                    retryMethod={pendingPay.current?.method ?? null}
                    onPay={(method, amount, ref, tip) => void pay(method, amount, ref, tip)}
                    onStripe={
                      stripeOn
                        ? (amount, tip) => setStripeCheckout({ paymentId: ulid(), amount, tip })
                        : undefined
                    }
                  />
                )}
                {isUnsettled(order) && draft.length > 0 && (
                  <p className="text-xs text-muted">Add the new items before taking payment.</p>
                )}
                {order.payments.length === 0 && !pendingPay.current && isUnsettled(order) && (
                  <VoidPanel
                    busy={busy === "void"}
                    retrying={pendingVoid.current !== null}
                    onVoid={voidOrder}
                  />
                )}
                {order.state === "paid" && refundable(order).length > 0 && (
                  <button
                    onClick={() => setShowRefund(true)}
                    disabled={busy !== null}
                    className="w-full rounded-xl px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-40 dark:text-red-300 dark:hover:bg-red-950/40"
                  >
                    Refund
                  </button>
                )}
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setShowReceipt(true)}
                    disabled={busy !== null}
                    className="rounded-xl border border-border px-4 py-2 text-sm font-medium hover:bg-background disabled:opacity-40"
                  >
                    Receipt
                  </button>
                  <button
                    onClick={newOrder}
                    disabled={busy !== null}
                    className="rounded-xl px-4 py-2 text-sm font-medium text-muted hover:bg-background disabled:opacity-40"
                  >
                    New order
                  </button>
                </div>
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
      {showOpen && (
        <OpenOrders
          load={loadOpenOrders}
          onPick={(o) => void openOrder(o.id)}
          onClose={() => setShowOpen(false)}
        />
      )}
      {showReceipt && order && (
        <ReceiptDialog
          load={() => api.receipt(till.token, order.id)}
          print={() => api.printReceipt(till.token, order.id)}
          onClose={() => setShowReceipt(false)}
        />
      )}
      {stripeCheckout && order && (
        <StripeCardDialog
          amountCents={stripeCheckout.amount}
          tipCents={stripeCheckout.tip}
          start={() =>
            api.stripeIntent(till.token, order.id, {
              payment_id: stripeCheckout.paymentId,
              amount_cents: stripeCheckout.amount,
              tip_cents: stripeCheckout.tip,
            })
          }
          onPaid={(intentId) => {
            const { paymentId, amount, tip } = stripeCheckout;
            setStripeCheckout(null);
            void pay("stripe", amount, undefined, tip, { paymentId, intentId });
          }}
          onClose={() => setStripeCheckout(null)}
        />
      )}
      {removing && (
        <RemoveItemDialog
          name={removing.name}
          sent={removing.sent}
          busy={busy === "remove"}
          onRemove={(reason) => void removeLine(removing, reason)}
          onClose={() => setRemoving(null)}
        />
      )}
      {showRefund && order && (
        <RefundDialog
          order={order}
          busy={busy === "refund"}
          onRefund={(choice) => void refund(choice)}
          onClose={() => setShowRefund(false)}
        />
      )}
      {pinAsk && (
        <PinPrompt
          title={pinAsk.title}
          error={pinAsk.error}
          onSubmit={(pin) => {
            pinAsk.resolve(pin);
            setPinAsk(null);
          }}
          onCancel={() => {
            pinAsk.resolve(null);
            setPinAsk(null);
          }}
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
