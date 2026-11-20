// Ringing up one order: build a draft from the menu, place it, send it to the kitchen, and
// take payment. Holds the draft + placed-order state and the safe-retry command wiring that
// web/src/app/page.tsx holds; the menu and ticket are two views it switches between.

import { useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { api, ApiError } from "../lib/api";
import { friendlyError, runCommand, type Pending } from "../lib/commands";
import { groupsFor, type ChosenModifier } from "../lib/menu";
import { formatCents } from "../lib/money";
import {
  draftItemFrom,
  orderLineCount,
  orderTotalCents,
  toLineIns,
  type DraftItem,
} from "../lib/order";
import type { PayMethod } from "../lib/payments";
import { isUnsettled, withKitchen, withPayment } from "../lib/placed";
import type {
  AddLinesRequest,
  CommandIn,
  KitchenResponse,
  OrderResponse,
  PayRequest,
  PlaceOrderRequest,
  TillMenuItem,
} from "../lib/types";
import { ulid } from "../lib/ulid";
import { REVOKED_NOTICE, useTill } from "../TillContext";
import { useMenu } from "../useMenu";
import { theme } from "../theme";
import { Badge, Banner, Muted } from "../ui";
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

type Busy = null | "place" | "add" | "kitchen" | "pay";

export function OrderWorkspace() {
  const { till, command, orderNumber, orderNumberAfter, seqAfter, unpair } = useTill();
  const token = till?.token;
  const locationId = till?.locationId;
  const { menu, error: menuError, now, reload } = useMenu(token, locationId, () =>
    unpair(REVOKED_NOTICE),
  );

  const [view, setView] = useState<"menu" | "ticket">("menu");
  const [draft, setDraft] = useState<DraftItem[]>([]);
  const [fulfillment, setFulfillment] = useState("dine_in");
  const [table, setTable] = useState("");
  const [customizing, setCustomizing] = useState<TillMenuItem | null>(null);
  const [placed, setPlaced] = useState<{ order: OrderResponse; kitchen?: KitchenResponse } | null>(
    null,
  );
  const [busy, setBusy] = useState<Busy>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  // A command's body (and so its ids) lives in a ref until the server answers, so tapping
  // again after a dropped connection resends the same ids and can't duplicate anything.
  const pendingPlace = useRef<PlaceOrderRequest | null>(null);
  const pendingAdd = useRef<AddLinesRequest | null>(null);
  const pendingKitchen = useRef<CommandIn | null>(null);
  const pendingPay = useRef<PayRequest | null>(null);

  // What render needs to know about the in-flight commands, mirrored from the refs (which are
  // the retry source of truth, mutated inside runCommand) into state so render reads state,
  // not refs. Resynced after every command settles.
  const [inflight, setInflight] = useState<{
    place: boolean;
    add: boolean;
    kitchen: boolean;
    payMethod: PayMethod | null;
    placeNumber: number | null;
  }>({ place: false, add: false, kitchen: false, payMethod: null, placeNumber: null });

  function syncInflight() {
    setInflight({
      place: pendingPlace.current !== null,
      add: pendingAdd.current !== null,
      kitchen: pendingKitchen.current !== null,
      payMethod: pendingPay.current?.method ?? null,
      placeNumber: pendingPlace.current?.order_number ?? null,
    });
  }

  const order = placed?.order ?? null;
  const unconfirmed = (order === null && inflight.place) || inflight.add;
  const canAdd = order !== null && isUnsettled(order) && !inflight.kitchen;
  const editable = !unconfirmed && (order === null || canAdd);
  const draftCount = orderLineCount(draft);
  const draftTotal = orderTotalCents(draft);

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

  function failed(err: unknown) {
    setActionError(friendlyError(err));
    if (err instanceof ApiError && STALE_MENU.has(err.code)) void reload();
  }

  /** Apply a server answer to the order it was about, never to one opened since. */
  function update(orderId: string, change: (p: { order: OrderResponse; kitchen?: KitchenResponse }) => {
    order: OrderResponse;
    kitchen?: KitchenResponse;
  }) {
    setPlaced((current) => (current && current.order.id === orderId ? change(current) : current));
  }

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
    setDraft((prev) =>
      prev.map((it) => (it.ref === ref ? { ...it, quantity: Math.max(1, it.quantity + delta) } : it)),
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
      setView("ticket");
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
      syncInflight();
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
      syncInflight();
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
      update(order.id, (p) => ({ ...p, order: fresh ?? withKitchen(p.order, kitchen), kitchen }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
      syncInflight();
    }
  }

  /** Pay the order. An unconfirmed earlier attempt is always resent as it was, so a dropped
   *  connection can't turn into a second charge. */
  async function pay(method: PayMethod, amountCents: number, externalRef?: string) {
    if (!till || !order) return;
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
          tip_cents: 0,
          external_ref: method === "external" ? externalRef : null,
          stripe_payment_intent_id: null,
        }),
        (body) => api.payOrder(till.token, order.id, body),
      );
      update(order.id, (p) => ({ ...p, order: withPayment(p.order, payment) }));
    } catch (err) {
      failed(err);
    } finally {
      setBusy(null);
      syncInflight();
    }
  }

  function newOrder() {
    pendingPlace.current = null;
    pendingAdd.current = null;
    pendingKitchen.current = null;
    pendingPay.current = null;
    setPlaced(null);
    setDraft([]);
    setActionError(null);
    setFulfillment("dine_in");
    setTable("");
    setView("menu");
    syncInflight();
  }

  const handlers: TicketHandlers = {
    onQtyChange: changeQty,
    onRemove: removeItem,
    onPlace: () => void placeOrder(),
    onAddToOrder: () => void addToOrder(),
    onSend: () => void sendToKitchen(),
    onPay: (method, amount, externalRef) => void pay(method, amount, externalRef),
    onNewOrder: newOrder,
  };

  const customizer =
    customizing && menu ? (
      <ItemCustomizer
        item={customizing}
        groups={groupsFor(customizing, menu)}
        now={now}
        onAdd={addItem}
        onClose={() => setCustomizing(null)}
      />
    ) : null;

  if (view === "ticket") {
    return (
      <View style={styles.fill}>
        <Pressable onPress={() => setView("menu")} style={styles.backRow} accessibilityRole="button">
          <Text style={styles.back}>‹ {editable ? "Add items" : "Menu"}</Text>
        </Pressable>
        <TicketScreen
          draft={draft}
          order={order}
          kitchen={placed?.kitchen}
          editable={editable}
          busy={busy !== null}
          actionError={actionError}
          unconfirmed={unconfirmed}
          retryMethod={inflight.payMethod}
          fulfillment={fulfillment}
          table={table}
          nextOrderNumber={inflight.placeNumber ?? till?.nextOrderNumber ?? 0}
          onFulfillment={setFulfillment}
          onTable={setTable}
          handlers={handlers}
        />
        {customizer}
      </View>
    );
  }

  return (
    <View style={styles.fill}>
      <ScrollView contentContainerStyle={styles.menuScroll} keyboardShouldPersistTaps="handled">
        {menuError ? <Banner kind="error">Couldn&apos;t load the menu: {menuError}</Banner> : null}
        {!menu ? (
          <Muted style={{ padding: theme.space(4) }}>Loading menu…</Muted>
        ) : (
          <MenuScreen items={menu.items} now={now} onPick={pickItem} />
        )}
      </ScrollView>
      <CartBar order={order} count={draftCount} total={draftTotal} onPress={() => setView("ticket")} />
      {customizer}
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
