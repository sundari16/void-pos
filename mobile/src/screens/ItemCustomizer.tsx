// Pick an item's quantity and modifiers before it goes on the order. Mirrors
// web/src/components/ItemDialog.tsx: a pick-one group swaps its choice, other groups take
// several (each with a count, up to the group's maximum), and Add waits until every required
// group has its choices. A note for the kitchen goes on as a free-text line.

import { useState } from "react";
import { Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import {
  chosenCount,
  chosenModifiers,
  groupOnSale,
  limitLabel,
  selectionProblems,
  setCount,
  toggle,
  windowLabel,
  type ChosenModifier,
  type Selection,
} from "../lib/menu";
import { formatCents, formatDelta } from "../lib/money";
import type { ModifierGroup, TillMenuItem } from "../lib/types";
import { theme } from "../theme";
import { Button, Muted } from "../ui";

export function ItemCustomizer({
  item,
  groups,
  now,
  onAdd,
  onClose,
}: {
  item: TillMenuItem;
  groups: ModifierGroup[];
  /** Local time at the location, "HH:MM:SS". */
  now: string;
  onAdd: (item: TillMenuItem, quantity: number, chosen: ChosenModifier[], note: string) => void;
  onClose: () => void;
}) {
  const [quantity, setQuantity] = useState(1);
  const [selection, setSelection] = useState<Selection>({});
  const [note, setNote] = useState("");

  const chosen = chosenModifiers(groups, selection);
  const modsEach = chosen.reduce((sum, c) => sum + c.modifier.price_delta_cents * c.quantity, 0);
  const subtotal = (item.base_price_cents + modsEach) * quantity;
  const problems = selectionProblems(groups, selection, now);

  return (
    <Modal visible animationType="slide" transparent onRequestClose={onClose}>
      <View style={styles.backdrop}>
        <Pressable style={styles.fill} onPress={onClose} accessibilityLabel="Close" />
        <View style={styles.sheet}>
          <View style={styles.header}>
            <View style={{ flex: 1 }}>
              <Text style={styles.title}>{item.name}</Text>
              <Muted>{formatCents(item.base_price_cents)}</Muted>
            </View>
            <Pressable onPress={onClose} accessibilityLabel="Close" hitSlop={10}>
              <Text style={styles.close}>×</Text>
            </Pressable>
          </View>

          <ScrollView contentContainerStyle={styles.body} keyboardShouldPersistTaps="handled">
            <View style={styles.row}>
              <Text style={styles.rowLabel}>Quantity</Text>
              <Stepper value={quantity} onChange={(q) => setQuantity(Math.max(1, Math.min(999, q)))} />
            </View>

            {groups.map((group) => (
              <GroupPicker
                key={group.id}
                group={group}
                onSale={groupOnSale(group, now)}
                selection={selection}
                onChange={setSelection}
              />
            ))}

            <View>
              <Text style={styles.sectionLabel}>Note for the kitchen</Text>
              <TextInput
                value={note}
                onChangeText={setNote}
                maxLength={200}
                placeholder="e.g. nut allergy"
                placeholderTextColor={theme.colors.muted}
                style={styles.note}
              />
            </View>
          </ScrollView>

          <View style={styles.footer}>
            {problems.map((p) => (
              <Text key={p} style={styles.problem}>
                {p}
              </Text>
            ))}
            <Button
              title={`Add to order   ${formatCents(subtotal)}`}
              onPress={() => onAdd(item, quantity, chosen, note)}
              disabled={problems.length > 0}
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}

function GroupPicker({
  group,
  onSale,
  selection,
  onChange,
}: {
  group: ModifierGroup;
  onSale: boolean;
  selection: Selection;
  onChange: (next: Selection) => void;
}) {
  const multiple = group.max_select !== 1;
  const full = group.max_select !== null && chosenCount(group, selection) >= group.max_select;
  const hours = windowLabel(group.available_from, group.available_to);

  return (
    <View style={{ opacity: onSale ? 1 : 0.6 }}>
      <View style={styles.legend}>
        <Text style={styles.sectionLabel}>{group.name}</Text>
        <Muted style={{ fontSize: 12 }}>
          {onSale ? limitLabel(group) : `Not available now${hours ? ` (${hours})` : ""}`}
        </Muted>
      </View>
      <View style={styles.chipWrap}>
        {group.modifiers.map((mod) => {
          const count = selection[mod.id] ?? 0;
          const delta = formatDelta(mod.price_delta_cents);
          const label = `${mod.name}${delta ? `  ${delta}` : ""}`;
          if (count && multiple) {
            return (
              <View key={mod.id} style={[styles.modChip, styles.modChipOn]}>
                <Pressable
                  onPress={() => onChange(setCount(group, selection, mod.id, count - 1))}
                  hitSlop={6}
                  accessibilityLabel={`One less ${mod.name}`}
                >
                  <Text style={styles.modStep}>−</Text>
                </Pressable>
                <Text style={styles.modChipOnText}>
                  {count > 1 ? `${count}× ` : ""}
                  {label}
                </Text>
                <Pressable
                  onPress={() => onChange(setCount(group, selection, mod.id, count + 1))}
                  disabled={full || !onSale}
                  hitSlop={6}
                  accessibilityLabel={`One more ${mod.name}`}
                >
                  <Text style={[styles.modStep, (full || !onSale) && { opacity: 0.4 }]}>＋</Text>
                </Pressable>
              </View>
            );
          }
          const disabled = !count && (!onSale || (full && multiple));
          return (
            <Pressable
              key={mod.id}
              onPress={() => onChange(toggle(group, selection, mod.id))}
              disabled={disabled}
              accessibilityState={{ selected: count > 0, disabled }}
              style={[styles.modChip, count > 0 && styles.modChipOn, disabled && { opacity: 0.4 }]}
            >
              <Text style={count > 0 ? styles.modChipOnText : styles.modChipText}>{label}</Text>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

function Stepper({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  return (
    <View style={styles.stepper}>
      <Pressable
        onPress={() => onChange(value - 1)}
        style={styles.stepperBtn}
        accessibilityLabel="Decrease quantity"
      >
        <Text style={styles.stepperText}>−</Text>
      </Pressable>
      <Text style={styles.stepperValue}>{value}</Text>
      <Pressable
        onPress={() => onChange(value + 1)}
        style={styles.stepperBtn}
        accessibilityLabel="Increase quantity"
      >
        <Text style={styles.stepperText}>＋</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.4)" },
  fill: { flex: 1 },
  sheet: {
    backgroundColor: theme.colors.surface,
    borderTopLeftRadius: theme.radius.lg,
    borderTopRightRadius: theme.radius.lg,
    maxHeight: "88%",
  },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    padding: theme.space(4),
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  title: { fontSize: 18, fontWeight: "700", color: theme.colors.text },
  close: { fontSize: 28, lineHeight: 28, color: theme.colors.muted, paddingHorizontal: 6 },
  body: { padding: theme.space(4), gap: theme.space(5) },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  rowLabel: { fontSize: 15, fontWeight: "600", color: theme.colors.text },
  legend: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    marginBottom: theme.space(2),
  },
  sectionLabel: {
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: theme.colors.muted,
    marginBottom: theme.space(2),
  },
  chipWrap: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  modChip: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.pill,
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: theme.colors.surface,
  },
  modChipOn: { backgroundColor: theme.colors.accent, borderColor: theme.colors.accent, gap: 8 },
  modChipText: { fontSize: 14, color: theme.colors.text },
  modChipOnText: { fontSize: 14, color: theme.colors.accentText, fontWeight: "500" },
  modStep: { fontSize: 18, color: theme.colors.accentText, paddingHorizontal: 2 },
  note: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(3),
    fontSize: 15,
    color: theme.colors.text,
  },
  footer: {
    padding: theme.space(4),
    borderTopWidth: 1,
    borderTopColor: theme.colors.border,
    gap: theme.space(2),
  },
  problem: { fontSize: 13, color: theme.colors.warn },
  stepper: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  stepperBtn: {
    width: 40,
    height: 40,
    borderRadius: theme.radius.md,
    borderWidth: 1,
    borderColor: theme.colors.border,
    alignItems: "center",
    justifyContent: "center",
  },
  stepperText: { fontSize: 20, color: theme.colors.text },
  stepperValue: { minWidth: 28, textAlign: "center", fontSize: 16, fontWeight: "700", color: theme.colors.text },
});
