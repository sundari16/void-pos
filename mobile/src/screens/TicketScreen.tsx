// The order ticket: the draft being built, then the placed order with send-to-kitchen and
// payment. Mirrors the aside in web/src/app/page.tsx and web/src/components/OrderTicket.tsx,
// trimmed to the mobile till's flow (place → send → cash / card-on-terminal → new order).
// Money stays in integer cents; amounts are only formatted for display.

import { useState } from "react";
import { ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { formatCents, formatDelta } from "../lib/money";
import { itemSubtotalCents, orderLineCount, orderTotalCents, type DraftItem } from "../lib/order";
import { centsToInput, parseDollars, providerLabel, terminalRef, type PayMethod } from "../lib/payments";
import { isUnsettled, placedItemCents, placedItems, sendLabel } from "../lib/placed";
import type { KitchenResponse, OrderResponse } from "../lib/types";
import { theme } from "../theme";
import { Badge, Banner, Button, Chip, Muted } from "../ui";

const FULFILLMENTS = ["dine_in", "pickup", "delivery"] as const;

export interface TicketHandlers {
  onQtyChange: (ref: string, delta: number) => void;
  onRemove: (ref: string) => void;
  onPlace: () => void;
  onAddToOrder: () => void;
  onSend: () => void;
  onPay: (method: PayMethod, amountCents: number, externalRef?: string) => void;
  onNewOrder: () => void;
}

export function TicketScreen({
  draft,
  order,
  kitchen,
  editable,
  busy,
  actionError,
  unconfirmed,
  retryMethod,
  fulfillment,
  table,
  nextOrderNumber,
  onFulfillment,
  onTable,
  handlers,
}: {
  draft: DraftItem[];
  order: OrderResponse | null;
  kitchen: KitchenResponse | undefined;
  editable: boolean;
  busy: boolean;
  actionError: string | null;
  unconfirmed: boolean;
  retryMethod: PayMethod | null;
  fulfillment: string;
  table: string;
  nextOrderNumber: number;
  onFulfillment: (f: string) => void;
  onTable: (t: string) => void;
  handlers: TicketHandlers;
}) {
  const draftTotal = orderTotalCents(draft);
  const draftCount = orderLineCount(draft);

  return (
    <View style={styles.fill}>
      <View style={styles.header}>
        <Text style={styles.heading}>
          {order?.table_label ? `Table ${order.table_label} ` : "Order "}
          <Text style={{ color: theme.colors.muted }}>
            #{order?.order_number ?? nextOrderNumber}
          </Text>
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
        {order ? <PlacedLines order={order} /> : null}
        {order && draft.length > 0 ? <Text style={styles.addingLabel}>Adding</Text> : null}
        {order === null || draft.length > 0 ? (
          <DraftLines
            items={draft}
            editable={editable}
            onQtyChange={handlers.onQtyChange}
            onRemove={handlers.onRemove}
          />
        ) : null}
      </ScrollView>

      <View style={styles.footer}>
        <View style={styles.totalRow}>
          {order ? (
            <>
              <Muted>
                Total {formatCents(order.total_cents)}
                {draft.length > 0 ? ` · adding ${formatCents(draftTotal)}` : ""}
              </Muted>
              <Text style={styles.total}>
                {order.balance_cents > 0 || isUnsettled(order)
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
          <Muted style={{ fontSize: 12 }}>
            Held as it is until the server confirms it; tap again to retry.
          </Muted>
        ) : null}
        {actionError ? <Banner kind="error">{actionError}</Banner> : null}

        {order && order.payments.length > 0 ? (
          <View style={styles.paidBox}>
            {order.payments.map((p) => (
              <Text key={p.payment_id} style={styles.paidText}>
                Paid {formatCents(p.amount_cents)} · {providerLabel(p.provider)}
                {p.tip_cents > 0 ? ` · tip ${formatCents(p.tip_cents)}` : ""}
              </Text>
            ))}
            <Text style={[styles.paidText, { fontWeight: "700" }]}>
              {order.balance_cents > 0 ? `${formatCents(order.balance_cents)} still owing` : "Paid in full"}
            </Text>
          </View>
        ) : null}

        {kitchen ? (
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
            title={busy ? "Placing…" : "Place order"}
            onPress={handlers.onPlace}
            disabled={draft.length === 0}
            loading={busy}
          />
        ) : (
          <View style={{ gap: theme.space(2) }}>
            {draft.length > 0 ? (
              <Button
                title={busy ? "Adding…" : `Add ${draftCount} item${draftCount === 1 ? "" : "s"} to the order`}
                onPress={handlers.onAddToOrder}
                loading={busy}
              />
            ) : null}
            {order.state !== "voided" ? (
              <Button
                title={busy ? "Sending…" : sendLabel(order)}
                onPress={handlers.onSend}
                disabled={draft.length > 0}
                variant={order.unsent_lines > 0 ? "outline" : "ghost"}
              />
            ) : null}
            {isUnsettled(order) && draft.length === 0 ? (
              <PaymentSection
                balanceCents={order.balance_cents}
                busy={busy}
                retryMethod={retryMethod}
                onPay={handlers.onPay}
              />
            ) : null}
            {isUnsettled(order) && draft.length > 0 ? (
              <Muted style={{ fontSize: 12 }}>Add the new items before taking payment.</Muted>
            ) : null}
            <Button title="New order" onPress={handlers.onNewOrder} variant="ghost" />
          </View>
        )}
      </View>
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

function PlacedLines({ order }: { order: OrderResponse }) {
  const items = placedItems(order.lines);
  if (items.length === 0) return null;
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
        </View>
      ))}
    </View>
  );
}

function PaymentSection({
  balanceCents,
  busy,
  retryMethod,
  onPay,
}: {
  balanceCents: number;
  busy: boolean;
  retryMethod: PayMethod | null;
  onPay: (method: PayMethod, amountCents: number, externalRef?: string) => void;
}) {
  const [amount, setAmount] = useState(centsToInput(balanceCents));
  const [lastBalance, setLastBalance] = useState(balanceCents);
  const [cardOpen, setCardOpen] = useState(false);
  const [cardRef, setCardRef] = useState("");
  const [error, setError] = useState<string | null>(null);

  // A payment went through: move the amount on to whatever is left.
  if (balanceCents !== lastBalance) {
    setLastBalance(balanceCents);
    setAmount(centsToInput(balanceCents));
    setCardOpen(false);
    setCardRef("");
    setError(null);
  }

  function checkedAmount(): number | null {
    const parsed = parseDollars(amount);
    if ("error" in parsed) {
      setError(parsed.error);
      return null;
    }
    if (parsed.cents > balanceCents) {
      setError(`That's more than the ${formatCents(balanceCents)} owing.`);
      return null;
    }
    if (parsed.cents === 0 && balanceCents > 0) {
      setError("Enter an amount above zero.");
      return null;
    }
    setError(null);
    return parsed.cents;
  }

  if (retryMethod) {
    return (
      <Button
        title={busy ? "Paying…" : `Retry ${retryMethod === "cash" ? "cash" : "card"} payment`}
        onPress={() => onPay(retryMethod, 0)}
        loading={busy}
      />
    );
  }

  return (
    <View style={styles.payBox}>
      <View style={styles.payHeader}>
        <Text style={styles.payLabel}>Amount</Text>
        <Muted>Owing {formatCents(balanceCents)}</Muted>
      </View>
      <View style={styles.amountField}>
        <Text style={styles.dollar}>$</Text>
        <TextInput
          value={amount}
          onChangeText={(t) => {
            setAmount(t);
            setError(null);
          }}
          keyboardType="decimal-pad"
          accessibilityLabel="Amount to pay"
          style={styles.amountInput}
        />
      </View>
      {error ? <Text style={styles.payError}>{error}</Text> : null}

      {cardOpen ? (
        <View style={{ gap: theme.space(2) }}>
          <Text style={styles.payLabel}>Terminal receipt or approval number</Text>
          <TextInput
            value={cardRef}
            onChangeText={setCardRef}
            autoCapitalize="characters"
            autoCorrect={false}
            style={styles.refInput}
            accessibilityLabel="Terminal reference"
          />
          <View style={styles.payButtons}>
            <Button
              title={busy ? "Recording…" : "Record card payment"}
              onPress={() => {
                const cents = checkedAmount();
                if (cents === null) return;
                const checked = terminalRef(cardRef);
                if ("error" in checked) {
                  setError(checked.error);
                  return;
                }
                onPay("external", cents, checked.ref);
              }}
              disabled={!cardRef.trim()}
              loading={busy}
              style={{ flex: 1 }}
            />
            <Button
              title="Cancel"
              onPress={() => {
                setCardOpen(false);
                setCardRef("");
              }}
              variant="outline"
              style={{ flex: 1 }}
            />
          </View>
        </View>
      ) : (
        <View style={styles.payButtons}>
          <Button
            title={busy ? "Paying…" : "Pay cash"}
            onPress={() => {
              const cents = checkedAmount();
              if (cents !== null) onPay("cash", cents);
            }}
            loading={busy}
            style={{ flex: 1 }}
          />
          <Button
            title="Card on terminal"
            onPress={() => {
              if (checkedAmount() !== null) setCardOpen(true);
            }}
            variant="outline"
            style={{ flex: 1 }}
          />
        </View>
      )}
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
  footer: {
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    padding: theme.space(4),
    gap: theme.space(3),
    backgroundColor: theme.colors.surface,
  },
  totalRow: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  total: { fontSize: 18, fontWeight: "700", color: theme.colors.text },
  paidBox: {
    borderRadius: theme.radius.md,
    backgroundColor: "#dcefe2",
    padding: theme.space(3),
    gap: 2,
  },
  paidText: { fontSize: 13, color: theme.colors.success },
  payBox: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    padding: theme.space(3),
    gap: theme.space(2),
  },
  payHeader: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  payLabel: { fontSize: 14, fontWeight: "600", color: theme.colors.text },
  amountField: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
  },
  dollar: { color: theme.colors.muted, fontSize: 16 },
  amountInput: { flex: 1, paddingVertical: theme.space(3), paddingHorizontal: 4, fontSize: 16, fontWeight: "600", color: theme.colors.text },
  payError: { fontSize: 13, color: theme.colors.danger },
  payButtons: { flexDirection: "row", gap: theme.space(2) },
  refInput: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(3),
    fontSize: 15,
    color: theme.colors.text,
    fontFamily: "monospace",
  },
});
