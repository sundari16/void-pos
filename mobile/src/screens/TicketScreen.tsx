// The order ticket: the draft being built, then the placed order with send-to-kitchen,
// payment (cash, card on the terminal, card on screen through Stripe; splits and tips),
// discounts and surcharges, taking items off, voids and refunds. Mirrors the aside in
// web/src/app/page.tsx. Money stays in integer cents; amounts are only formatted for display.

import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import type { NewAdjustment } from "../flows";
import { formatCents, formatDelta } from "../lib/money";
import { itemSubtotalCents, orderLineCount, orderTotalCents, type DraftItem } from "../lib/order";
import { providerLabel, type PayMethod } from "../lib/payments";
import { isUnsettled, placedItemCents, placedItems, refundable, sendLabel } from "../lib/placed";
import type { KitchenResponse, OrderLineOut, OrderResponse, VoidResponse } from "../lib/types";
import { theme } from "../theme";
import { Badge, Banner, Button, Chip, Muted } from "../ui";
import { AdjustPanel } from "./AdjustPanel";
import { PaymentPanel } from "./PaymentPanel";

const FULFILLMENTS = ["dine_in", "pickup", "delivery"] as const;

export interface TicketHandlers {
  onQtyChange: (ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
  onPlace: () => void;
  onAddToOrder: () => void;
  onSend: () => void;
  onPay: (method: PayMethod, amountCents: number, externalRef?: string, tipCents?: number) => void;
  /** Absent when Stripe isn't set up on the server. */
  onCard?: (amountCents: number, tipCents: number) => void;
  onRemoveLine: (line: OrderLineOut) => void;
  onAddAdjustment: (adj: NewAdjustment) => void;
  onRemoveAdjustment: (adjustmentId: string) => void;
  onVoid: () => void;
  onRefund: () => void;
  onNewOrder: () => void;
}

export interface TicketState {
  draft: DraftItem[];
  order: OrderResponse | null;
  kitchen: KitchenResponse | undefined;
  /** The last send was queued: it prints when the server is back. */
  kitchenQueued: boolean;
  voided: VoidResponse | undefined;
  editable: boolean;
  busy: string | null;
  actionError: string | null;
  unconfirmed: boolean;
  retryMethod: PayMethod | null;
  retryVoid: boolean;
  /** This order has changes the server hasn't had yet. */
  queuedHere: boolean;
  offline: boolean;
  holidaySurchargeBp: number | null | undefined;
  fulfillment: string;
  table: string;
  nextOrderNumber: number;
}

export function TicketScreen({
  state,
  onFulfillment,
  onTable,
  handlers,
}: {
  state: TicketState;
  onFulfillment: (f: string) => void;
  onTable: (t: string) => void;
  handlers: TicketHandlers;
}) {
  const { draft, order, kitchen, editable, actionError, unconfirmed, fulfillment, table } = state;
  const busy = state.busy !== null;
  const draftTotal = orderTotalCents(draft);
  const draftCount = orderLineCount(draft);
  const unsettled = order !== null && isUnsettled(order);
  const canChange = unsettled && draft.length === 0 && !busy && !state.retryVoid;

  return (
    <View style={styles.fill}>
      <View style={styles.header}>
        <Text style={styles.heading}>
          {order?.table_label ? `Table ${order.table_label} ` : "Order "}
          <Text style={{ color: theme.colors.muted }}>#{order?.order_number ?? state.nextOrderNumber}</Text>
        </Text>
        {order ? <Badge state={order.state} /> : null}
      </View>

      {!order ? (
        <View style={styles.optionsRow}>
          {FULFILLMENTS.map((f) => (
            <Chip
              key={f}
              label={f.replace("_", "-")}
              selected={fulfillment === f}
              disabled={!editable}
              onPress={() => onFulfillment(f)}
            />
          ))}
          {fulfillment === "dine_in" ? (
            <TextInput
              value={table}
              onChangeText={(t) => onTable(t.slice(0, 40))}
              editable={editable}
              placeholder="Table"
              placeholderTextColor={theme.colors.muted}
              style={styles.table}
              accessibilityLabel="Table"
            />
          ) : null}
        </View>
      ) : null}

      <ScrollView contentContainerStyle={styles.lines} keyboardShouldPersistTaps="handled">
        {order ? <PlacedLines order={order} onRemove={canChange ? handlers.onRemoveLine : undefined} /> : null}
        {order && draft.length > 0 ? <Text style={styles.addingLabel}>Adding</Text> : null}
        {order === null || draft.length > 0 ? (
          <DraftLines
            items={draft}
            editable={editable}
            onQtyChange={handlers.onQtyChange}
            onRemove={handlers.onRemove}
          />
        ) : null}
        {order && unsettled && draft.length === 0 ? (
          <View style={{ marginTop: theme.space(3) }}>
            <AdjustPanel
              adjustments={order.adjustments}
              holidaySurchargeBp={state.holidaySurchargeBp}
              busy={busy}
              offline={state.offline}
              onAdd={handlers.onAddAdjustment}
              onRemove={handlers.onRemoveAdjustment}
            />
          </View>
        ) : null}
        {order && !unsettled
          ? order.adjustments.map((a) => (
              <View key={a.id} style={styles.adjRow}>
                <Muted>{a.label ?? (a.kind === "discount" ? "Discount" : "Surcharge")}</Muted>
                <Muted>{formatCents(a.amount_cents)}</Muted>
              </View>
            ))
          : null}
      </ScrollView>

      <ScrollView style={styles.footerScroll} contentContainerStyle={styles.footer} keyboardShouldPersistTaps="handled">
        <View style={styles.totalRow}>
          {order ? (
            <>
              <Muted>
                Total {formatCents(order.total_cents)}
                {draft.length > 0 ? ` · adding ${formatCents(draftTotal)}` : ""}
              </Muted>
              <Text style={styles.total}>
                {order.balance_cents > 0 || unsettled
                  ? `${formatCents(order.balance_cents)} owing`
                  : formatCents(order.total_cents)}
              </Text>
            </>
          ) : (
            <>
              <Muted>
                {draftCount} item{draftCount === 1 ? "" : "s"}
              </Muted>
              <Text style={styles.total}>{formatCents(draftTotal)}</Text>
            </>
          )}
        </View>

        {unconfirmed ? (
          <Muted style={{ fontSize: 12 }}>Held as it is until the server confirms it; tap again to retry.</Muted>
        ) : null}
        {state.queuedHere ? (
          <Banner kind="info">Saved on this till. It reaches the server as soon as the till is back online.</Banner>
        ) : null}
        {actionError ? <Banner kind="error">{actionError}</Banner> : null}

        {state.voided ? (
          <Banner kind="error">
            {`Voided. ${
              state.voided.printed
                ? "A void ticket went to the kitchen."
                : order?.lines.some((l) => l.sent) && !state.voided.replayed
                  ? "No void ticket printed: tell the kitchen."
                  : ""
            }`}
          </Banner>
        ) : null}

        {order && order.payments.length > 0 ? (
          <View style={styles.paidBox}>
            {order.payments.map((p) => (
              <Text key={p.payment_id} style={styles.paidText}>
                Paid {formatCents(p.amount_cents)} · {providerLabel(p.provider)}
                {p.tip_cents > 0 ? ` · tip ${formatCents(p.tip_cents)}` : ""}
              </Text>
            ))}
            {order.refunds.map((r) => (
              <Text key={r.refund_id} style={[styles.paidText, { color: theme.colors.danger }]}>
                Refunded {formatCents(r.amount_cents)} · {providerLabel(r.provider)} · {r.reason}
              </Text>
            ))}
            <Text style={[styles.paidText, { fontWeight: "700" }]}>
              {order.state === "refunded"
                ? "Refunded in full"
                : order.balance_cents > 0
                  ? `${formatCents(order.balance_cents)} still owing`
                  : "Paid in full"}
            </Text>
          </View>
        ) : null}

        {state.kitchenQueued ? (
          <Muted style={{ fontSize: 12 }}>Kitchen send saved: the ticket prints when the till is back online.</Muted>
        ) : kitchen ? (
          <Muted style={{ fontSize: 12 }}>
            {kitchen.replayed
              ? "Already sent to the kitchen."
              : kitchen.printed
                ? kitchen.lines_sent
                  ? "Ticket printed in the kitchen."
                  : "Kitchen ticket reprinted."
                : "Marked sent. No kitchen printer is set up on the server."}
          </Muted>
        ) : null}

        {!order ? (
          <Button
            title={state.busy === "place" ? "Placing…" : "Place order"}
            onPress={handlers.onPlace}
            disabled={draft.length === 0}
            loading={state.busy === "place"}
          />
        ) : (
          <View style={{ gap: theme.space(2) }}>
            {draft.length > 0 ? (
              <Button
                title={state.busy === "add" ? "Adding…" : `Add ${draftCount} item${draftCount === 1 ? "" : "s"} to the order`}
                onPress={handlers.onAddToOrder}
                loading={state.busy === "add"}
              />
            ) : null}
            {order.state !== "voided" ? (
              <Button
                title={state.busy === "kitchen" ? "Sending…" : sendLabel(order)}
                onPress={handlers.onSend}
                disabled={draft.length > 0 || busy}
                variant={order.unsent_lines > 0 ? "outline" : "ghost"}
              />
            ) : null}
            {unsettled && !state.retryVoid && draft.length === 0 ? (
              <PaymentPanel
                balanceCents={order.balance_cents}
                busy={state.busy === "pay" || state.busy === "card"}
                retryMethod={state.retryMethod}
                offline={state.offline}
                onPay={handlers.onPay}
                onCard={handlers.onCard}
              />
            ) : null}
            {unsettled && draft.length > 0 ? (
              <Muted style={{ fontSize: 12 }}>Add the new items before taking payment.</Muted>
            ) : null}
            {unsettled && order.payments.length === 0 && !state.retryMethod ? (
              <Button
                title={
                  state.busy === "void"
                    ? "Voiding…"
                    : state.retryVoid
                      ? "Retry void"
                      : state.offline
                        ? "Void order (needs the server)"
                        : "Void order"
                }
                onPress={handlers.onVoid}
                disabled={busy || (state.offline && !state.retryVoid)}
                variant="danger"
              />
            ) : null}
            {order.state === "paid" && refundable(order).length > 0 ? (
              <Button
                title={state.offline ? "Refund (needs the server)" : "Refund"}
                onPress={handlers.onRefund}
                disabled={busy || state.offline}
                variant="danger"
              />
            ) : null}
            <Button title="New order" onPress={handlers.onNewOrder} variant="ghost" disabled={busy} />
          </View>
        )}
      </ScrollView>
    </View>
  );
}

function DraftLines({
  items,
  editable,
  onQtyChange,
  onRemove,
}: {
  items: DraftItem[];
  editable: boolean;
  onQtyChange: (ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
}) {
  if (items.length === 0) {
    return <Muted style={styles.hint}>Tap a menu item to start the order.</Muted>;
  }
  return (
    <View>
      {items.map((item) => (
        <View key={item.ref} style={styles.line}>
          <View style={styles.lineTop}>
            <View style={{ flex: 1 }}>
              <Text style={styles.lineName}>{item.name}</Text>
              {item.modifiers.map((mod) => (
                <Text key={mod.ref} style={styles.modLine}>
                  + {mod.quantity > 1 ? `${mod.quantity}× ` : ""}
                  {mod.name}
                  {mod.unitPriceCents !== 0 ? `  ${formatDelta(mod.unitPriceCents * mod.quantity)}` : ""}
                </Text>
              ))}
            </View>
            <Text style={styles.amount}>{formatCents(itemSubtotalCents(item))}</Text>
          </View>
          {editable ? (
            <View style={styles.lineActions}>
              <View style={styles.qty}>
                <QtyButton label="−" onPress={() => onQtyChange(item.ref, -1)} name={item.name} />
                <Text style={styles.qtyValue}>{item.quantity}</Text>
                <QtyButton label="＋" onPress={() => onQtyChange(item.ref, 1)} name={item.name} />
              </View>
              <Text style={styles.remove} onPress={() => onRemove(item.ref)}>
                Remove
              </Text>
            </View>
          ) : (
            <Muted style={{ marginTop: 6 }}>Qty {item.quantity}</Muted>
          )}
        </View>
      ))}
    </View>
  );
}

function QtyButton({ label, onPress, name }: { label: string; onPress: () => void; name: string }) {
  return (
    <Text accessibilityRole="button" accessibilityLabel={`Change ${name}`} onPress={onPress} style={styles.qtyBtn}>
      {label}
    </Text>
  );
}

function PlacedLines({ order, onRemove }: { order: OrderResponse; onRemove?: (line: OrderLineOut) => void }) {
  const items = placedItems(order.lines);
  if (items.length === 0) return <Muted style={styles.hint}>Every item has been taken off.</Muted>;
  return (
    <View>
      {items.map(({ line, modifiers }) => (
        <View key={line.id} style={styles.line}>
          <View style={styles.lineTop}>
            <View style={{ flex: 1 }}>
              <Text style={styles.lineName}>
                {line.quantity > 1 ? `${line.quantity}× ` : ""}
                {line.name}
                {line.sent ? "" : "  •"}
              </Text>
              {modifiers.map((mod) => (
                <Text key={mod.id} style={styles.modLine}>
                  + {mod.quantity > 1 ? `${mod.quantity}× ` : ""}
                  {mod.name}
                </Text>
              ))}
            </View>
            <Text style={styles.amount}>{formatCents(placedItemCents({ line, modifiers }))}</Text>
          </View>
          {onRemove ? (
            <Pressable onPress={() => onRemove(line)} accessibilityRole="button" accessibilityLabel={`Take off ${line.name}`}>
              <Text style={[styles.remove, { marginTop: 6 }]}>Take off</Text>
            </Pressable>
          ) : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: theme.space(4),
    paddingTop: theme.space(3),
    paddingBottom: theme.space(2),
  },
  heading: { fontSize: 18, fontWeight: "700", color: theme.colors.text },
  optionsRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    alignItems: "center",
    gap: theme.space(2),
    paddingHorizontal: theme.space(4),
    paddingBottom: theme.space(2),
  },
  table: {
    minWidth: 72,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(2),
    color: theme.colors.text,
  },
  lines: { paddingHorizontal: theme.space(4), paddingBottom: theme.space(2) },
  hint: { paddingVertical: theme.space(10), textAlign: "center" },
  addingLabel: {
    marginTop: theme.space(3),
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: theme.colors.muted,
  },
  line: { paddingVertical: theme.space(3), borderBottomWidth: 1, borderBottomColor: theme.colors.border },
  lineTop: { flexDirection: "row", alignItems: "flex-start", justifyContent: "space-between", gap: theme.space(2) },
  lineName: { fontSize: 15, fontWeight: "600", color: theme.colors.text },
  modLine: { marginTop: 2, fontSize: 13, color: theme.colors.muted },
  amount: { fontSize: 15, color: theme.colors.text },
  lineActions: { marginTop: theme.space(2), flexDirection: "row", alignItems: "center", gap: theme.space(4) },
  qty: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  qtyBtn: {
    width: 32,
    height: 32,
    lineHeight: 30,
    textAlign: "center",
    fontSize: 18,
    color: theme.colors.text,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
  },
  qtyValue: { minWidth: 20, textAlign: "center", fontSize: 15, color: theme.colors.text },
  remove: { fontSize: 14, color: theme.colors.danger },
  adjRow: { flexDirection: "row", justifyContent: "space-between", marginTop: theme.space(2) },
  footerScroll: {
    flexGrow: 0,
    maxHeight: "60%",
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
  },
  footer: { padding: theme.space(4), gap: theme.space(3) },
  totalRow: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  total: { fontSize: 18, fontWeight: "700", color: theme.colors.text },
  paidBox: {
    borderRadius: theme.radius.md,
    backgroundColor: "#dcefe2",
    padding: theme.space(3),
    gap: 2,
  },
  paidText: { fontSize: 13, color: theme.colors.success },
});
