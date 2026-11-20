// The menu, grouped by category. Items not on sale now stay visible, greyed out with why
// ("Unavailable", "11:00–15:00"), so staff can still answer questions about them. Mirrors
// web/src/components/MenuGrid.tsx.

import { Pressable, StyleSheet, Text, View } from "react-native";
import { itemOnSale, windowLabel } from "../lib/menu";
import { formatCents } from "../lib/money";
import type { TillMenuItem } from "../lib/types";
import { theme } from "../theme";
import { Muted } from "../ui";

const UNCATEGORIZED = "Other";

function groupByCategory(items: TillMenuItem[]): [string, TillMenuItem[]][] {
  const groups = new Map<string, TillMenuItem[]>();
  for (const item of items) {
    const key = item.category?.trim() || UNCATEGORIZED;
    const bucket = groups.get(key);
    if (bucket) bucket.push(item);
    else groups.set(key, [item]);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (a === UNCATEGORIZED) return 1;
    if (b === UNCATEGORIZED) return -1;
    return a.localeCompare(b);
  });
}

export function MenuScreen({
  items,
  now,
  onPick,
}: {
  items: TillMenuItem[];
  /** Local time at the location, "HH:MM:SS". */
  now: string;
  onPick: (item: TillMenuItem) => void;
}) {
  if (items.length === 0) {
    return <Muted style={styles.empty}>No menu items for this location yet.</Muted>;
  }

  return (
    <View style={{ gap: theme.space(5) }}>
      {groupByCategory(items).map(([category, group]) => (
        <View key={category}>
          <Text style={styles.category}>{category}</Text>
          <View style={styles.grid}>
            {group.map((item) => {
              const onSale = itemOnSale(item, now);
              const why = !item.is_available
                ? "Unavailable"
                : windowLabel(item.available_from, item.available_to);
              return (
                <Pressable
                  key={item.id}
                  onPress={() => onPick(item)}
                  disabled={!onSale}
                  accessibilityRole="button"
                  style={({ pressed }) => [
                    styles.tile,
                    !onSale && styles.tileOff,
                    pressed && onSale && { borderColor: theme.colors.accent },
                  ]}
                >
                  <Text style={styles.tileName}>{item.name}</Text>
                  <View style={styles.tileFoot}>
                    <Muted>{formatCents(item.base_price_cents)}</Muted>
                    {!onSale && why ? <Text style={styles.tileWhy}>{why}</Text> : null}
                  </View>
                </Pressable>
              );
            })}
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  empty: { padding: theme.space(6), textAlign: "center" },
  category: {
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: theme.colors.muted,
    marginBottom: theme.space(2),
  },
  grid: { flexDirection: "row", flexWrap: "wrap", gap: theme.space(2) },
  tile: {
    width: "48%",
    minHeight: 84,
    flexGrow: 1,
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.surface,
    padding: theme.space(3),
    justifyContent: "space-between",
  },
  tileOff: { opacity: 0.45 },
  tileName: { fontSize: 15, fontWeight: "600", color: theme.colors.text },
  tileFoot: {
    marginTop: theme.space(2),
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
  },
  tileWhy: { fontSize: 11, color: theme.colors.muted },
});
