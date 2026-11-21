// The Void POS mobile till. Pair the device, then ring up orders (menu → ticket → place →
// send to kitchen → pay, plus discounts, voids, refunds and open orders) and read the shift
// closeout. Mirrors the web till's flows and the backend's API contracts (see
// src/void_pos/api); keeps working offline through the on-device outbox (src/offline). Money
// is integer cents, formatted to AUD for display only.

import { StatusBar } from "expo-status-bar";
import { useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  SafeAreaView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SyncProvider, useSync } from "./src/SyncContext";
import { TillProvider, useTill } from "./src/TillContext";
import { CloseoutScreen } from "./src/screens/CloseoutScreen";
import { OrderWorkspace } from "./src/screens/OrderWorkspace";
import { PairScreen } from "./src/screens/PairScreen";
import { SyncBadge } from "./src/screens/SyncStatus";
import { theme } from "./src/theme";

export default function App() {
  return (
    <TillProvider>
      <SyncProvider>
        <StatusBar style="dark" />
        <Root />
      </SyncProvider>
    </TillProvider>
  );
}

function Root() {
  const { till, ready } = useTill();
  if (!ready) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={theme.colors.accent} />
      </View>
    );
  }
  if (!till) {
    return (
      <SafeAreaView style={styles.fill}>
        <PairScreen />
      </SafeAreaView>
    );
  }
  return <Main />;
}

function Main() {
  const { till, unpair } = useTill();
  const { queued } = useSync();
  const [tab, setTab] = useState<"order" | "closeout">("order");

  function confirmUnpair() {
    const waiting =
      queued > 0
        ? ` ${queued} change${queued === 1 ? "" : "s"} taken offline haven't reached the server yet; they stay on this device and go once it is paired again as the same till.`
        : "";
    Alert.alert("Unpair this till?", `You'll need a new pairing code from the venue admin to use it again.${waiting}`, [
      { text: "Cancel", style: "cancel" },
      { text: "Unpair", style: "destructive", onPress: () => unpair() },
    ]);
  }

  return (
    <SafeAreaView style={styles.fill}>
      <View style={styles.topbar}>
        <View style={{ flex: 1 }}>
          <Text style={styles.venue} numberOfLines={1}>
            {till?.locationName}
          </Text>
          <Text style={styles.device} numberOfLines={1}>
            {till?.deviceName}
          </Text>
        </View>
        <View style={styles.actions}>
          <SyncBadge />
          <Pressable onPress={confirmUnpair} hitSlop={8} accessibilityRole="button">
            <Text style={styles.unpair}>Unpair</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.tabs}>
        <Tab label="Order" active={tab === "order"} onPress={() => setTab("order")} />
        <Tab label="Closeout" active={tab === "closeout"} onPress={() => setTab("closeout")} />
      </View>

      <View style={styles.fill}>{tab === "order" ? <OrderWorkspace /> : <CloseoutScreen />}</View>
    </SafeAreaView>
  );
}

function Tab({ label, active, onPress }: { label: string; active: boolean; onPress: () => void }) {
  return (
    <Pressable
      onPress={onPress}
      style={[styles.tab, active && styles.tabActive]}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
    >
      <Text style={[styles.tabText, active && styles.tabTextActive]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: theme.colors.bg },
  center: { flex: 1, alignItems: "center", justifyContent: "center", backgroundColor: theme.colors.bg },
  topbar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: theme.space(4),
    paddingVertical: theme.space(3),
    backgroundColor: theme.colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  venue: { fontSize: 16, fontWeight: "700", color: theme.colors.text },
  device: { fontSize: 12, color: theme.colors.muted },
  unpair: { fontSize: 14, color: theme.colors.danger, fontWeight: "600" },
  actions: { flexDirection: "row", alignItems: "center", gap: theme.space(3) },
  tabs: {
    flexDirection: "row",
    backgroundColor: theme.colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
  },
  tab: { flex: 1, paddingVertical: theme.space(3), alignItems: "center", borderBottomWidth: 2, borderBottomColor: "transparent" },
  tabActive: { borderBottomColor: theme.colors.accent },
  tabText: { fontSize: 15, fontWeight: "600", color: theme.colors.muted },
  tabTextActive: { color: theme.colors.accent },
});
