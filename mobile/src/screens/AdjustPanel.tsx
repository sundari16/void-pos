// The order's discounts and surcharges, and buttons to add or take them off. A discount asks
// for a manager's PIN once the venue has a manager (OrderWorkspace handles that), so it needs
// the server; the public-holiday surcharge doesn't. Mirrors web/src/components/AdjustPanel.tsx.

import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { DISCOUNT_PERCENTS, discountFrom, holidaySurcharge, type NewAdjustment } from "../flows";
import { formatCents } from "../lib/money";
import { adjustmentLabel, percentText } from "../lib/placed";
import type { AdjustmentOut } from "../lib/types";
import { theme } from "../theme";
import { Button, Chip, ErrorText, Input, Muted } from "../ui";

export function AdjustPanel({
  adjustments,
  holidaySurchargeBp,
  busy,
  offline,
  onAdd,
  onRemove,
}: {
  adjustments: AdjustmentOut[];
  holidaySurchargeBp: number | null | undefined;
  busy: boolean;
  offline: boolean;
  onAdd: (adj: NewAdjustment) => void;
  onRemove: (adjustmentId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [percent, setPercent] = useState<number | null>(1000);
  const [amount, setAmount] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const hasHoliday = adjustments.some((a) => a.kind === "surcharge" && a.label === "Public holiday");

  function submit() {
    const adj = discountFrom(percent, amount, label);
    if ("error" in adj) return setError(adj.error);
    setError(null);
    onAdd(adj);
    setOpen(false);
    setLabel("");
    setAmount("");
  }

  return (
    <View style={{ gap: theme.space(2) }}>
      {adjustments.map((a) => (
        <View key={a.id} style={styles.row}>
          <Text style={styles.text}>{adjustmentLabel(a)}</Text>
          <View style={styles.row}>
            <Text style={styles.text}>{formatCents(a.amount_cents)}</Text>
            <Pressable
              onPress={() => onRemove(a.id)}
              disabled={busy}
              accessibilityRole="button"
              accessibilityLabel={`Take off ${adjustmentLabel(a)}`}
              hitSlop={8}
            >
              <Text style={styles.remove}>×</Text>
            </Pressable>
          </View>
        </View>
      ))}
      {open ? (
        <View style={styles.box}>
          <Text style={styles.title}>Discount</Text>
          <View style={styles.chips}>
            {DISCOUNT_PERCENTS.map((bp) => (
              <Chip key={bp} label={percentText(bp)} selected={percent === bp} onPress={() => setPercent(bp)} />
            ))}
            <Chip label="$ amount" selected={percent === null} onPress={() => setPercent(null)} />
          </View>
          {percent === null ? (
            <Input
              value={amount}
              onChangeText={setAmount}
              keyboardType="decimal-pad"
              placeholder="5.00"
              accessibilityLabel="Discount amount"
            />
          ) : null}
          <Input
            value={label}
            onChangeText={(t) => setLabel(t.slice(0, 80))}
            placeholder="Reason (e.g. Staff meal)"
            accessibilityLabel="Discount reason"
          />
          {error ? <ErrorText>{error}</ErrorText> : null}
          <View style={styles.buttons}>
            <Button title="Apply discount" onPress={submit} disabled={busy} style={styles.flex} />
            <Button title="Cancel" onPress={() => setOpen(false)} variant="outline" style={styles.flex} />
          </View>
        </View>
      ) : (
        <View style={styles.chips}>
          <Chip label={offline ? "Discount (needs the server)" : "Discount"} disabled={busy || offline} onPress={() => setOpen(true)} />
          {holidaySurchargeBp ? (
            <Chip
              label={`Public holiday surcharge ${percentText(holidaySurchargeBp)}`}
              disabled={busy || hasHoliday}
              onPress={() => onAdd(holidaySurcharge(holidaySurchargeBp))}
            />
          ) : null}
        </View>
      )}
      {open && offline ? <Muted>A discount needs the server (a manager approves it).</Muted> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: theme.space(2) },
  text: { fontSize: 14, color: theme.colors.text },
  remove: { fontSize: 18, color: theme.colors.muted, paddingHorizontal: theme.space(1) },
  title: { fontSize: 14, fontWeight: "600", color: theme.colors.text },
  box: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    padding: theme.space(3),
    gap: theme.space(2),
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  buttons: { flexDirection: "row", gap: theme.space(2) },
});
