// Ringing up one order: build a draft from the menu, place it, send it to the kitchen, take
// payment, and the rest of what the web till does (discounts and surcharges, taking items
// off, voids and refunds with a manager's PIN, card on screen through Stripe, picking an open
// order back up). Holds the order state and the safe-retry wiring that web/src/app/page.tsx
// holds, plus the offline path: while the server is out of reach, a change the till may make
// on its own is saved on the device (SyncContext) and pushed when the server is back.

import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { mergeOpenOrders, withApproval, type NewAdjustment, type RefundChoice } from "../flows";
import { ApiError } from "../lib/api";
import { friendlyError, runCommand, type Pending } from "../lib/commands";
import { groupsFor, type ChosenModifier } from "../lib/menu";
import { formatCents } from "../lib/money";
import { draftItemFrom, orderLineCount, orderTotalCents, toLineIns, type DraftItem } from "../lib/order";
import type { PayMethod } from "../lib/payments";
import { isUnsettled, withKitchen, withPayment, withVoid } from "../lib/placed";
import type {
  AddLinesRequest,
  AdjustRequest,
  CommandIn,
  KitchenResponse,
  OrderLineOut,
  OrderResponse,
  OrderSummary,
  PayRequest,
  PlaceOrderRequest,
  RefundRequest,
  RemoveLinesRequest,
  TillMenuItem,
  VoidRequest,
  VoidResponse,
} from "../lib/types";
import { ulid } from "../lib/ulid";
import { api } from "../net";
import {
  addLinesEvent,
  adjustEvent,
  kitchenEvent,
  needsServer,
  payEvent,
  placeEvent,
  removeAdjustmentEvent,
  removeLinesEvent,
} from "../offline/events";
import { summaryOf } from "../offline/projection";
import { isNetworkError } from "../offline/sync";
import { takeCardOnScreen } from "../stripeSheet";
import { useSync } from "../SyncContext";
import { REVOKED_NOTICE, useTill } from "../TillContext";
import { useMenu } from "../useMenu";
import { theme } from "../theme";
import { Badge, Banner, Button, Muted } from "../ui";
import { OpenOrdersSheet, PinPrompt, RefundSheet, RemoveItemSheet, VoidSheet } from "./Dialogs";
import { ItemCustomizer } from "./ItemCustomizer";
import { MenuScreen } from "./MenuScreen";
import { TicketScreen, type TicketHandlers } from "./TicketScreen";

// A refused order that means the till's menu is out of date: reload it.
const STALE_MENU = new Set([
  "item_unavailable",
  "modifier_unavailable",
  "modifier_not_allowed",
  "modifier_selection",
]);

type Busy = null | "place" | "add" | "kitchen" | "pay" | "card" | "void" | "open" | "remove" | "adjust" | "refund";

interface Placed {
  order: OrderResponse;
  kitchen?: KitchenResponse;
  kitchenQueued?: boolean;
  voided?: VoidResponse;
}

interface PinAsk {
  title: string;
  error?: string;
  resolve: (pin: string | null) => void;
}

/** A command's answer: the server's, or the order as the till saved it to send later. */
type Outcome<R> = { queued: false; result: R } | { queued: true; order: OrderResponse };

export function OrderWorkspace() {
  const { till, command, orderNumber, orderNumberAfter, seqAfter, unpair } = useTill();
  const sync = useSync();
  const token = till?.token;
  const locationId = till?.locationId;
  const { menu, error: menuError, now, reload } = useMenu(token, locationId, () => unpair(REVOKED_NOTICE));

  const [view, setView] = useState<"menu" | "ticket">("menu");
  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState("dine_in");
  const [table, setTable] = useState("");
  const [customizing, setCustomizing] = useState<TillMenuItem | null>(null);
  const [placed, setPlaced] = useState<Placed | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<null | "void" | "refund" | "open">(null);
  const [removing, setRemoving] = useState<OrderLineOut | null>(null);
  const [pinAsk, setPinAsk] = useState<PinAsk | null>(null);
  const [stripeOn, setStripeOn] = useState(false);

  // A command's body (and so its ids) lives in a ref until the server answers, so tapping
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
  // A card payment through Stripe in progress: one payment id for the intent and the
  // recorded payment, so a retry reuses both.
  const cardCheckout = useRef<{ paymentId: string; amount: number; tip: number } | null>(null);

  // What render needs to know about the in-flight commands, mirrored from the refs (the retry
  // source of truth, mutated inside runCommand) into state so render reads state, not refs.
  const [inflight, setInflight] = useState({
    place: false,
    add: false,
    kitchen: false,
    void: false,
    payMethod: null as PayMethod | null,
    placeNumber: null as number | null,
  });
  function syncInflight() {
    setInflight({
      place: pendingPlace.current !== null,
      add: pendingAdd.current !== null,
      kitchen: pendingKitchen.current !== null,
      void: pendingVoid.current !== null,
      payMethod: pendingPay.current?.method ?? null,
      placeNumber: pendingPlace.current?.order_number ?? null,
    });
  }

  useEffect(() => {
    if (!token) return;
    api
      .stripeConfig(token)
      .then((c) => setStripeOn(c.enabled))
      .catch(() => setStripeOn(false));
  }, [token]);

  const order = placed?.order ?? null;
  const offline = !sync.online;
  const unconfirmed = (order === null && inflight.place) || inflight.add;
  const canAdd = order !== null && isUnsettled(order) && !inflight.kitchen && !inflight.void;
  const editable = !unconfirmed && (order === null || canAdd);
  const draftCount = orderLineCount(draft);
  const draftTotal = orderTotalCents(draft);

  // The sync refreshed or pulled this order: show it, unless a command for it is in flight.
  const orderId = order?.id;
  const { subscribe, localOrder } = sync;
  useEffect(() => {
    if (!orderId) return;
    return subscribe((ids) => {
      if (!ids.includes(orderId)) return;
      const refs = [pendingAdd, pendingKitchen, pendingPay, pendingVoid, pendingRemove, pendingAdjust, pendingUnadjust, pendingRefund];
      if (refs.some((r) => r.current !== null)) return;
      void localOrder(orderId).then((fresh) => {
        if (fresh) setPlaced((p) => (p && p.order.id === orderId ? { ...p, order: fresh } : p));
      });
    });
  }, [orderId, subscribe, localOrder]);

  /** Run a command online, or save it to send later when the server is out of reach and
   *  `offline` says how. Without `offline` the change needs the server (a manager's PIN,
   *  Stripe): it waits for this order's queued changes to sync first. */
  async function exec<B extends CommandIn, R>(o: {
    pending: Pending<B>;
    build: () => B;
    online: (body: B) => Promise<R>;
    offline?: (body: B) => Promise<OrderResponse>;
    orderId: string | null;
    what: string;
    repair?: (err: ApiError, body: B) => B | null;
  }): Promise<Outcome<R>> {
    const queuedHere = o.orderId !== null && (await sync.hasQueued(o.orderId));
    if (!o.offline) {
      if (offline && o.pending.current === null) throw needsServer(o.what);
      if (queuedHere) {
        await sync.syncNow();
        if (o.orderId && (await sync.hasQueued(o.orderId))) {
          throw new ApiError(409, "sync_pending", "This order's offline changes haven't reached the server yet. Try again in a moment.");
        }
      }
    }
    const queueFirst = o.offline !== undefined && (offline || queuedHere);
    const saved: { order: OrderResponse | null } = { order: null };
    const result = await runCommand<B, R | null>({
      pending: o.pending,
      build: o.build,
      seqAfter,
      onUnauthorized: () => unpair(REVOKED_NOTICE),
      repair: o.repair,
      send: async (body) => {
        if (queueFirst && o.offline) {
          saved.order = await o.offline(body);
          return null;
        }
        try {
          return await o.online(body);
        } catch (err) {
          if (!isNetworkError(err)) throw err;
          sync.wentOffline();
          if (!o.offline) throw err;
          saved.order = await o.offline(body);
          return null;
        }
      },
    });
    if (saved.order) return { queued: true, order: saved.order };
    return { queued: false, result: result as R };
  }

  function failed(err: unknown) {
    setActionError(friendlyError(err));
    if (err instanceof ApiError && STALE_MENU.has(err.code)) void reload();
  }

  /** Apply an answer to the order it was about, never to one opened since. */
  function update(id: string, change: (p: Placed) => Placed) {
    setPlaced((current) => (current && current.order.id === id ? change(current) : current));
  }

  /** Keep the server's answer on the device too, so the till can carry on with it offline. */
  function keep(view: OrderResponse) {
    void sync.remember(view);
  }

  function askPin(title: string, error?: string): Promise<string | null> {
    return new Promise((resolve) => setPinAsk({ title, error, resolve }));
  }

  const approve = <T,>(title: string, attempt: (pin?: string) => Promise<T>) =>
    withApproval((error) => askPin(title, error), attempt);

  const identity = sync.identity;

  function pickItem(item: TillMenuItem) {
    if (busy !== null || !editable) return;
    setCustomizing(item);
  }

  function addItem(item: TillMenuItem, quantity: number, chosen: ChosenModifier[], note: string) {
    if (!editable) return;
    setDraft((prev) => [...prev, draftItemFrom(item, quantity, chosen, note)]);
    setCustomizing(null);
  }

  function changeQty(ref: string, delta: number) {
    if (!editable) return;
    setDraft((prev) => prev.map((it) => (it.ref === ref ? { ...it, quantity: Math.max(1, it.quantity + delta) } : it)));
  }

  function removeItem(ref: string) {
    if (!editable) return;
    setDraft((prev) => prev.filter((it) => it.ref !== ref));
  }

  async function run(kind: Exclude<Busy, null>, task: () => Promise<void>) {
    setBusy(kind);
    setActionError(null);
    try {
      await task();
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
      syncInflight();
    }
  }

  const placeOrder = () =>
    run("place", async () => {
      if (!till || !identity || draft.length === 0) return;
      const out = await exec({
        pending: pendingPlace,
        build: (): PlaceOrderRequest => ({
          ...command(),
          order_id: ulid(),
          order_number: orderNumber(),
          channel: "pos",
          fulfillment,
          table_label: fulfillment === "dine_in" && table.trim() ? table.trim() : null,
          lines: toLineIns(draft),
        }),
        online: (body) => api.placeOrder(till.token, body),
        offline: (body) => sync.record("push", null, null, () => placeEvent(identity, body)),
        orderId: null,
        what: "Placing an order",
        repair: (err, body) => {
          const next = err.body.next_order_number;
          return err.code === "order_number_used" && typeof next === "number"
            ? { ...body, order_number: orderNumberAfter(next) }
            : null;
        },
      });
      const view = out.queued ? out.order : out.result;
      if (!out.queued) keep(view);
      setPlaced({ order: view });
      setDraft([]);
      setView("ticket");
    });

  const addToOrder = () =>
    run("add", async () => {
      if (!till || !identity || !order || draft.length === 0) return;
      const out = await exec({
        pending: pendingAdd,
        build: (): AddLinesRequest => ({ ...command(), lines: toLineIns(draft) }),
        online: (body) => api.addLines(till.token, order.id, body),
        offline: (body) => sync.record("push", order.id, order, (base) => addLinesEvent(identity, base ?? order, body)),
        orderId: order.id,
        what: "Adding items",
      });
      const view = out.queued ? out.order : out.result;
      if (!out.queued) keep(view);
      update(order.id, (p) => ({ ...p, order: view }));
      setDraft([]);
    });

  const sendToKitchen = () =>
    run("kitchen", async () => {
      if (!till || !identity || !order) return;
      const out = await exec({
        pending: pendingKitchen,
        build: command,
        online: (body) => api.sendToKitchen(till.token, order.id, body),
        offline: (body) => sync.record("kitchen", order.id, order, (base) => kitchenEvent(identity, base ?? order, body)),
        orderId: order.id,
        what: "Sending to the kitchen",
      });
      if (out.queued) {
        update(order.id, (p) => ({ ...p, order: out.order, kitchen: undefined, kitchenQueued: true }));
        return;
      }
      const kitchen = out.result;
      // A replay doesn't say which lines that earlier send covered: ask the server.
      const fresh = kitchen.replayed ? await api.getOrder(till.token, order.id).catch(() => null) : null;
      const next = fresh ?? withKitchen(order, kitchen);
      keep(next);
      update(order.id, (p) => ({ ...p, order: fresh ?? withKitchen(p.order, kitchen), kitchen, kitchenQueued: false }));
    });

  /** Pay the order. An unconfirmed earlier attempt is always resent as it was, whichever
   *  button is pressed, so a dropped connection can't turn into a second charge. */
  const pay = (
    method: PayMethod,
    amountCents: number,
    externalRef?: string,
    tipCents = 0,
    stripe?: { paymentId: string; intentId: string },
  ) =>
    run("pay", async () => {
      if (!till || !identity || !order) return;
      const retrying = pendingPay.current?.method ?? method;
      const out = await exec({
        pending: pendingPay,
        build: (): PayRequest => ({
          ...command(),
          payment_id: stripe?.paymentId ?? ulid(),
          method,
          amount_cents: amountCents,
          tip_cents: tipCents,
          external_ref: method === "external" ? externalRef : null,
          stripe_payment_intent_id: stripe?.intentId ?? null,
        }),
        online: (body) => api.payOrder(till.token, order.id, body),
        offline:
          retrying === "stripe"
            ? undefined
            : (body) => sync.record("push", order.id, order, (base) => payEvent(identity, base ?? order, body)),
        orderId: order.id,
        what: "A card payment through Stripe",
      });
      if (out.queued) {
        update(order.id, (p) => ({ ...p, order: out.order }));
        return;
      }
      if (out.result.provider === "stripe") cardCheckout.current = null;
      keep(withPayment(order, out.result));
      update(order.id, (p) => ({ ...p, order: withPayment(p.order, out.result) }));
    });

  /** A card on this screen through Stripe: start the intent, let the customer pay on
   *  Stripe's sheet, then record it (the server captures it as it records it). */
  const payByCard = (amountCents: number, tipCents: number) =>
    run("card", async () => {
      if (!till || !order) return;
      const checkout = cardCheckout.current ?? { paymentId: ulid(), amount: amountCents, tip: tipCents };
      cardCheckout.current = checkout;
      try {
        if (!sync.online) throw needsServer("A card payment through Stripe");
        if (await sync.hasQueued(order.id)) {
          await sync.syncNow();
          if (await sync.hasQueued(order.id)) {
            throw new ApiError(409, "sync_pending", "This order's offline changes haven't reached the server yet. Try again in a moment.");
          }
        }
        const intent = await api.stripeIntent(till.token, order.id, {
          payment_id: checkout.paymentId,
          amount_cents: checkout.amount,
          tip_cents: checkout.tip,
        });
        const card = await takeCardOnScreen(intent, till.locationName);
        if (!card.authorised) {
          cardCheckout.current = null;
          if (card.error) setActionError(card.error);
          return;
        }
        setBusy(null);
        await pay("stripe", checkout.amount, undefined, checkout.tip, {
          paymentId: checkout.paymentId,
          intentId: intent.payment_intent_id,
        });
      } catch (err) {
        if (isNetworkError(err)) sync.wentOffline();
        cardCheckout.current = null;
        throw err;
      }
    });

  /** Void the placed order, with a manager's PIN once the venue has a manager. A retry
   *  after a dropped connection resends the first attempt, reason and all. */
  const voidOrder = (reason: string) =>
    run("void", async () => {
      if (!till || !order) return;
      const voided = await approve(`Void order #${order.order_number}`, (pin) =>
        exec({
          pending: pendingVoid,
          build: (): VoidRequest => ({ ...command(), reason, manager_pin: pin ?? null }),
          online: (body) => api.voidOrder(till.token, order.id, body),
          orderId: order.id,
          what: "Voiding an order",
        }),
      );
      if (!voided || voided.queued) return;
      const result = voided.result;
      keep(withVoid(order, result));
      update(order.id, (p) => ({ ...p, order: withVoid(p.order, result), voided: result }));
    });

  /** Take an item off: a manager's PIN if the kitchen has it (and the venue has managers);
   *  an item the kitchen hasn't had can come off offline. */
  const removeLine = (line: OrderLineOut, reason: string) =>
    run("remove", async () => {
      if (!till || !identity || !order) return;
      const out = await approve(`Take off ${line.name}`, (pin) =>
        exec({
          pending: pendingRemove,
          build: (): RemoveLinesRequest => ({ ...command(), line_ids: [line.id], reason, manager_pin: pin ?? null }),
          online: (body) => api.removeLines(till.token, order.id, body),
          offline: line.sent
            ? undefined
            : (body) => sync.record("push", order.id, order, (base) => removeLinesEvent(identity, base ?? order, body)),
          orderId: order.id,
          what: "Taking off an item the kitchen has",
        }),
      );
      if (!out) return;
      const view = out.queued ? out.order : out.result;
      if (!out.queued) keep(view);
      update(order.id, (p) => ({ ...p, order: view }));
    });

  const addAdjustment = (adj: NewAdjustment) =>
    run("adjust", async () => {
      if (!till || !identity || !order) return;
      const title = adj.kind === "discount" ? "Approve the discount" : "Add the surcharge";
      const out = await approve(title, (pin) =>
        exec({
          pending: pendingAdjust,
          build: (): AdjustRequest => ({ ...command(), id: ulid(), ...adj, manager_pin: pin ?? null }),
          online: (body) => api.adjust(till.token, order.id, body),
          offline:
            adj.kind === "discount"
              ? undefined
              : (body) => sync.record("push", order.id, order, (base) => adjustEvent(identity, base ?? order, body)),
          orderId: order.id,
          what: "A discount",
        }),
      );
      if (!out) return;
      const view = out.queued ? out.order : out.result;
      if (!out.queued) keep(view);
      update(order.id, (p) => ({ ...p, order: view }));
    });

  const takeOffAdjustment = (adjustmentId: string) =>
    run("adjust", async () => {
      if (!till || !identity || !order) return;
      const out = await exec({
        pending: pendingUnadjust,
        build: command,
        online: (body) => api.removeAdjustment(till.token, order.id, adjustmentId, body),
        offline: (body) =>
          sync.record("push", order.id, order, (base) => removeAdjustmentEvent(identity, base ?? order, adjustmentId, body)),
        orderId: order.id,
        what: "Taking off an adjustment",
      });
      const view = out.queued ? out.order : out.result;
      if (!out.queued) keep(view);
      update(order.id, (p) => ({ ...p, order: view }));
    });

  const refund = (choice: RefundChoice) =>
    run("refund", async () => {
      if (!till || !order) return;
      const out = await approve(`Refund order #${order.order_number}`, (pin) =>
        exec({
          pending: pendingRefund,
          build: (): RefundRequest => ({
            ...command(),
            refund_id: ulid(),
            payment_id: choice.paymentId,
            amount_cents: choice.amountCents,
            reason: choice.reason,
            external_ref: choice.externalRef,
            manager_pin: pin ?? null,
          }),
          online: (body) => api.refund(till.token, order.id, body),
          orderId: order.id,
          what: "A refund",
        }),
      );
      if (!out || out.queued) return;
      keep(out.result);
      update(order.id, (p) => ({ ...p, order: out.result }));
    });

  const { online: isOnline, localOpenOrders, queuedOrders, wentOffline } = sync;
  const loadOpenOrders = useCallback(async (): Promise<OrderSummary[]> => {
    const local = await localOpenOrders();
    if (!token || !locationId || !isOnline) return local.map(summaryOf);
    try {
      return mergeOpenOrders(await api.openOrders(token, locationId), local, queuedOrders);
    } catch (err) {
      if (!isNetworkError(err)) throw err;
      wentOffline();
      return local.map(summaryOf);
    }
  }, [token, locationId, isOnline, localOpenOrders, queuedOrders, wentOffline]);

  function confirmLeave(): Promise<boolean> {
    const message =
      order === null && draft.length > 0
        ? "Discard the order you're building?"
        : order && draft.length > 0
          ? `Discard the items not yet added to order #${order.order_number}?`
          : order && order.payments.length > 0 && order.balance_cents > 0
            ? `${formatCents(order.balance_cents)} is still owing on order #${order.order_number}. It stays in Open orders. Leave it for now?`
            : null;
    if (!message) return Promise.resolve(true);
    return new Promise((resolve) =>
      Alert.alert("Leave this order?", message, [
        { text: "Stay", style: "cancel", onPress: () => resolve(false) },
        { text: "Leave", style: "destructive", onPress: () => resolve(true) },
      ], { cancelable: true, onDismiss: () => resolve(false) }),
    );
  }

  function reset(next: Placed | null) {
    for (const ref of [
      pendingPlace,
      pendingAdd,
      pendingKitchen,
      pendingPay,
      pendingVoid,
      pendingRemove,
      pendingAdjust,
      pendingUnadjust,
      pendingRefund,
    ] as Pending<unknown>[]) {
      ref.current = null;
    }
    cardCheckout.current = null;
    setRemoving(null);
    setSheet(null);
    setPlaced(next);
    setDraft([]);
    setActionError(null);
    setFulfillment("dine_in");
    setTable("");
    syncInflight();
  }

  async function newOrder() {
    if (!(await confirmLeave())) return;
    reset(null);
    setView("menu");
  }

  async function openOrder(summary: OrderSummary) {
    if (!till || !(await confirmLeave())) return;
    setSheet(null);
    await run("open", async () => {
      let fresh: OrderResponse | null = null;
      if (sync.online && !(await sync.hasQueued(summary.id))) {
        try {
          fresh = await sync.remember(await api.getOrder(till.token, summary.id));
        } catch (err) {
          if (!isNetworkError(err)) throw err;
          sync.wentOffline();
        }
      }
      fresh ??= await sync.localOrder(summary.id);
      if (!fresh) throw new Error("This till doesn't have that order yet. Try again once it's back online.");
      reset({ order: fresh });
      setView("ticket");
    });
  }

  const handlers: TicketHandlers = {
    onQtyChange: changeQty,
    onRemove: removeItem,
    onPlace: () => void placeOrder(),
    onAddToOrder: () => void addToOrder(),
    onSend: () => void sendToKitchen(),
    onPay: (method, amount, externalRef, tip) => void pay(method, amount, externalRef, tip),
    onCard: stripeOn ? (amount, tip) => void payByCard(amount, tip) : undefined,
    onRemoveLine: setRemoving,
    onAddAdjustment: (adj) => void addAdjustment(adj),
    onRemoveAdjustment: (id) => void takeOffAdjustment(id),
    onVoid: () => (inflight.void ? void voidOrder("") : setSheet("void")),
    onRefund: () => setSheet("refund"),
    onNewOrder: () => void newOrder(),
  };

  const overlays = (
    <>
      {customizing && menu ? (
        <ItemCustomizer
          item={customizing}
          groups={groupsFor(customizing, menu)}
          now={now}
          onAdd={addItem}
          onClose={() => setCustomizing(null)}
        />
      ) : null}
      {sheet === "void" && order ? (
        <VoidSheet
          orderNumber={order.order_number}
          busy={busy === "void"}
          onVoid={(reason) => {
            setSheet(null);
            void voidOrder(reason);
          }}
          onClose={() => setSheet(null)}
        />
      ) : null}
      {sheet === "refund" && order ? (
        <RefundSheet
          order={order}
          busy={busy === "refund"}
          onRefund={(choice) => {
            setSheet(null);
            void refund(choice);
          }}
          onClose={() => setSheet(null)}
        />
      ) : null}
      {sheet === "open" ? (
        <OpenOrdersSheet
          load={loadOpenOrders}
          offline={offline}
          onPick={(o) => void openOrder(o)}
          onClose={() => setSheet(null)}
        />
      ) : null}
      {removing ? (
        <RemoveItemSheet
          name={removing.name}
          sent={removing.sent}
          busy={busy === "remove"}
          onRemove={(reason) => {
            const line = removing;
            setRemoving(null);
            void removeLine(line, reason);
          }}
          onClose={() => setRemoving(null)}
        />
      ) : null}
      {pinAsk ? (
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
      ) : null}
    </>
  );

  if (view === "ticket") {
    return (
      <View style={styles.fill}>
        <Pressable onPress={() => setView("menu")} style={styles.backRow} accessibilityRole="button">
          <Text style={styles.back}>‹ {editable ? "Add items" : "Menu"}</Text>
        </Pressable>
        <TicketScreen
          state={{
            draft,
            order,
            kitchen: placed?.kitchen,
            kitchenQueued: placed?.kitchenQueued ?? false,
            voided: placed?.voided,
            editable,
            busy,
            actionError,
            unconfirmed,
            retryMethod: inflight.payMethod,
            retryVoid: inflight.void,
            queuedHere: order !== null && sync.queuedOrders.has(order.id),
            offline,
            holidaySurchargeBp: till?.holidaySurchargeBp,
            fulfillment,
            table,
            nextOrderNumber: inflight.placeNumber ?? till?.nextOrderNumber ?? 0,
          }}
          onFulfillment={setFulfillment}
          onTable={setTable}
          handlers={handlers}
        />
        {overlays}
      </View>
    );
  }

  return (
    <View style={styles.fill}>
      <ScrollView contentContainerStyle={styles.menuScroll} keyboardShouldPersistTaps="handled">
        <View style={styles.toolbar}>
          <Button
            title={busy === "open" ? "Opening…" : "Open orders"}
            onPress={() => setSheet("open")}
            disabled={busy !== null}
            variant="outline"
            style={{ flex: 1 }}
          />
        </View>
        {actionError && view === "menu" ? <Banner kind="error">{actionError}</Banner> : null}
        {menuError ? (
          <Banner kind="error">
            {menu ? "Couldn't refresh the menu" : "Couldn't load the menu"}: {menuError}
          </Banner>
        ) : null}
        {!menu ? (
          <Muted style={{ padding: theme.space(4) }}>Loading menu…</Muted>
        ) : (
          <MenuScreen items={menu.items} now={now} onPick={pickItem} />
        )}
      </ScrollView>
      <CartBar order={order} count={draftCount} total={draftTotal} onPress={() => setView("ticket")} />
      {overlays}
    </View>
  );
}

function CartBar({
  order,
  count,
  total,
  onPress,
}: {
  order: OrderResponse | null;
  count: number;
  total: number;
  onPress: () => void;
}) {
  const left = order ? `Order #${order.order_number}` : `${count} item${count === 1 ? "" : "s"}`;
  const right = order
    ? order.balance_cents > 0 || isUnsettled(order)
      ? `${formatCents(order.balance_cents)} owing`
      : formatCents(order.total_cents)
    : formatCents(total);
  return (
    <Pressable onPress={onPress} style={styles.cartBar} accessibilityRole="button">
      <View style={styles.cartSide}>
        <Text style={styles.cartText}>{left}</Text>
        {order ? <Badge state={order.state} /> : null}
      </View>
      <View style={styles.cartSide}>
        <Text style={styles.cartText}>{right}</Text>
        <Text style={styles.cartChevron}>›</Text>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  menuScroll: { padding: theme.space(4), paddingBottom: theme.space(6), gap: theme.space(3) },
  toolbar: { flexDirection: "row", gap: theme.space(2) },
  cartBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    backgroundColor: theme.colors.accent,
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(4),
  },
  cartSide: { flexDirection: "row", alignItems: "center", gap: theme.space(2) },
  cartText: { color: theme.colors.accentText, fontSize: 16, fontWeight: "700" },
  cartChevron: { color: theme.colors.accentText, fontSize: 22, fontWeight: "700" },
  backRow: { paddingHorizontal: theme.space(4), paddingTop: theme.space(3) },
  back: { color: theme.colors.accent, fontSize: 16, fontWeight: "600" },
});
