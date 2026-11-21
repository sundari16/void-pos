// The order's side flows as sheets: manager PIN, voiding an order, taking an item off,
// refunding a payment, and picking an open order back up. Mirrors the web till's
// PinPrompt, VoidPanel, RemoveItemDialog, RefundDialog and OpenOrders; the checks live in
// ../flows.ts.

import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { REMOVE_REASONS, VOID_REASONS, cleanPin, refundFrom, validPin, type RefundChoice } from "../flows";
import { friendlyError } from "../lib/commands";
import { formatCents } from "../lib/money";
import { centsToInput, providerLabel } from "../lib/payments";
import { refundable } from "../lib/placed";
import type { OrderResponse, OrderSummary } from "../lib/types";
import { theme } from "../theme";
import { Button, Chip, ErrorText, Input, Muted, Overlay, Sheet } from "../ui";

/** Ask a manager for their PIN. The PIN is held only long enough to send it once. */
export function PinPrompt({
  title,
  error,
  onSubmit,
  onCancel,
}: {
  title: string;
  error?: string;
  onSubmit: (pin: string) => void;
  onCancel: () => void;
}) {
  const [pin, setPin] = useState("");
  return (
    <Overlay title={title} onClose={onCancel}>
      <Muted>Manager PIN</Muted>
      <Input
        value={pin}
        onChangeText={(t) => setPin(cleanPin(t))}
        secureTextEntry
        keyboardType="number-pad"
        autoFocus
        accessibilityLabel="Manager PIN"
        style={styles.pin}
        onSubmitEditing={() => validPin(pin) && onSubmit(pin)}
      />
      {error ? <ErrorText>{error}</ErrorText> : null}
      <View style={styles.row}>
        <Button title="Approve" onPress={() => onSubmit(pin)} disabled={!validPin(pin)} style={styles.flex} />
        <Button title="Cancel" onPress={onCancel} variant="outline" style={styles.flex} />
      </View>
    </Overlay>
  );
}

/** A reason from the common ones, or typed. */
function ReasonPicker({
  reasons,
  value,
  onChange,
  placeholder = "Other reason",
}: {
  reasons: string[];
  value: { choice: string | null; other: string };
  onChange: (v: { choice: string | null; other: string }) => void;
  placeholder?: string;
}) {
  return (
    <>
      <View style={styles.chips}>
        {reasons.map((r) => (
          <Chip
            key={r}
            label={r}
            selected={value.choice === r}
            onPress={() => onChange({ ...value, choice: value.choice === r ? null : r })}
          />
        ))}
      </View>
      <Input
        value={value.other}
        onChangeText={(t) => onChange({ choice: null, other: t.slice(0, 200) })}
        placeholder={placeholder}
        accessibilityLabel={placeholder}
      />
    </>
  );
}

const reasonOf = (v: { choice: string | null; other: string }) => (v.choice ?? v.other).trim();

export function VoidSheet({
  orderNumber,
  busy,
  onVoid,
  onClose,
}: {
  orderNumber: number;
  busy: boolean;
  onVoid: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState<{ choice: string | null; other: string }>({ choice: null, other: "" });
  return (
    <Sheet title={`Void order #${orderNumber}`} onClose={onClose}>
      <Muted>Why is this order being voided?</Muted>
      <ReasonPicker reasons={VOID_REASONS} value={reason} onChange={setReason} />
      <View style={styles.row}>
        <Button
          title={busy ? "Voiding…" : "Void order"}
          onPress={() => onVoid(reasonOf(reason))}
          disabled={!reasonOf(reason)}
          loading={busy}
          variant="danger"
          style={styles.flex}
        />
        <Button title="Keep order" onPress={onClose} variant="outline" style={styles.flex} />
      </View>
    </Sheet>
  );
}

export function RemoveItemSheet({
  name,
  sent,
  busy,
  onRemove,
  onClose,
}: {
  name: string;
  /** The kitchen has it: a CANCEL ticket prints, and a manager approves. */
  sent: boolean;
  busy: boolean;
  onRemove: (reason: string) => void;
  onClose: () => void;
}) {
  const [reason, setReason] = useState<{ choice: string | null; other: string }>({ choice: null, other: "" });
  return (
    <Sheet title={`Take off ${name}`} onClose={onClose}>
      {sent ? <Muted>The kitchen has this: a CANCEL ticket prints, and a manager approves.</Muted> : null}
      <ReasonPicker reasons={REMOVE_REASONS} value={reason} onChange={setReason} />
      <View style={styles.row}>
        <Button
          title={busy ? "Taking off…" : "Take it off"}
          onPress={() => onRemove(reasonOf(reason))}
          disabled={!reasonOf(reason)}
          loading={busy}
          variant="danger"
          style={styles.flex}
        />
        <Button title="Keep it" onPress={onClose} variant="outline" style={styles.flex} />
      </View>
    </Sheet>
  );
}

/** Give money back against one of a paid order's payments, the same way it was paid. */
export function RefundSheet({
  order,
  busy,
  onRefund,
  onClose,
}: {
  order: OrderResponse;
  busy: boolean;
  onRefund: (choice: RefundChoice) => void;
  onClose: () => void;
}) {
  const options = refundable(order);
  const [paymentId, setPaymentId] = useState(options[0]?.payment.payment_id ?? "");
  const picked = options.find((o) => o.payment.payment_id === paymentId);
  const [amount, setAmount] = useState(picked ? centsToInput(picked.left) : "");
  const [reason, setReason] = useState("");
  const [ref, setRef] = useState("");
  const [error, setError] = useState<string | null>(null);

  function choose(id: string) {
    setPaymentId(id);
    const next = options.find((o) => o.payment.payment_id === id);
    if (next) setAmount(centsToInput(next.left));
  }

  function submit() {
    if (!picked) return;
    const choice = refundFrom(picked, amount, reason, ref);
    if ("error" in choice) return setError(choice.error);
    setError(null);
    onRefund(choice);
  }

  return (
    <Sheet title={`Refund order #${order.order_number}`} onClose={onClose}>
      {options.length === 0 ? (
        <Muted>Everything taken has already been given back.</Muted>
      ) : (
        <>
          <Muted>Which payment</Muted>
          <View style={styles.chips}>
            {options.map(({ payment, left }) => (
              <Chip
                key={payment.payment_id}
                label={`${providerLabel(payment.provider)} ${formatCents(payment.amount_cents + payment.tip_cents)}${
                  left < payment.amount_cents + payment.tip_cents ? ` (${formatCents(left)} left)` : ""
                }`}
                selected={payment.payment_id === paymentId}
                onPress={() => choose(payment.payment_id)}
              />
            ))}
          </View>
          <Input value={amount} onChangeText={setAmount} keyboardType="decimal-pad" accessibilityLabel="Refund amount" />
          <Input
            value={reason}
            onChangeText={(t) => setReason(t.slice(0, 200))}
            placeholder="Why (e.g. Cold food)"
            accessibilityLabel="Refund reason"
          />
          {picked?.payment.provider === "external" ? (
            <>
              <Input
                value={ref}
                onChangeText={setRef}
                placeholder="Terminal refund reference"
                autoCapitalize="characters"
                autoCorrect={false}
                accessibilityLabel="Terminal refund reference"
              />
              <Muted style={{ fontSize: 12 }}>Do the refund on the card terminal first, then type its reference here.</Muted>
            </>
          ) : null}
          {picked?.payment.provider === "stripe" ? (
            <Muted style={{ fontSize: 12 }}>The money goes back to the customer&apos;s card through Stripe.</Muted>
          ) : null}
        </>
      )}
      {error ? <ErrorText>{error}</ErrorText> : null}
      <View style={styles.row}>
        <Button
          title={busy ? "Refunding…" : "Refund"}
          onPress={submit}
          disabled={options.length === 0}
          loading={busy}
          variant="danger"
          style={styles.flex}
        />
        <Button title="Cancel" onPress={onClose} variant="outline" style={styles.flex} />
      </View>
    </Sheet>
  );
}

/** The location's open and sent orders, newest first; tap one to carry on with it. */
export function OpenOrdersSheet({
  load,
  offline,
  onPick,
  onClose,
}: {
  load: () => Promise<OrderSummary[]>;
  offline: boolean;
  onPick: (order: OrderSummary) => void;
  onClose: () => void;
}) {
  const [orders, setOrders] = useState<OrderSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    load()
      .then((list) => active && setOrders(list))
      .catch((err) => active && setError(friendlyError(err)));
    return () => {
      active = false;
    };
  }, [load]);

  return (
    <Sheet title="Open orders" onClose={onClose}>
      {offline ? <Muted>Offline: showing the orders this till has. Others&apos; latest changes arrive when it&apos;s back online.</Muted> : null}
      {error ? <ErrorText>{error}</ErrorText> : null}
      {orders === null && !error ? <Muted>Loading…</Muted> : null}
      {orders?.length === 0 ? <Muted>No open orders.</Muted> : null}
      {orders?.map((o) => (
        <Pressable key={o.id} onPress={() => onPick(o)} style={styles.orderRow} accessibilityRole="button">
          <View style={styles.flex}>
            <Text style={styles.orderTitle}>
              #{o.order_number} · {o.table_label ? `Table ${o.table_label}` : o.fulfillment.replace("_", "-")}
            </Text>
            <Muted>
              {timeOf(o.created_at)} · {o.state}
              {o.unsent_lines > 0 ? " · new items not sent" : ""}
            </Muted>
          </View>
          <View style={{ alignItems: "flex-end" }}>
            <Text style={styles.orderTitle}>{formatCents(o.balance_cents)}</Text>
            {o.balance_cents !== o.total_cents ? <Muted>of {formatCents(o.total_cents)}</Muted> : null}
          </View>
        </Pressable>
      ))}
    </Sheet>
  );
}

function timeOf(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  row: { flexDirection: "row", gap: theme.space(2) },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  pin: { textAlign: "center", fontSize: 24, letterSpacing: 8 },
  orderRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(3),
    paddingVertical: theme.space(3),
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  orderTitle: { fontSize: 15, fontWeight: "600", color: theme.colors.text },
});
