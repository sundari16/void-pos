// The till's connection at a glance (online, offline with N changes saved, syncing, or
// changes the server refused), and a sheet with the detail: what's waiting, what was
// refused (to note and discard), and what the last sync had to say.

import { useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSync } from "../SyncContext";
import { theme } from "../theme";
import { Banner, Button, Muted, Sheet } from "../ui";

const TYPE_LABELS: Record<string, string> = {
  "order.placed": "Order placed",
  "order.lines_added": "Items added",
  "order.sent": "Sent to the kitchen",
  "order.paid": "Payment",
  "order.lines_removed": "Item taken off",
  "order.adjusted": "Surcharge",
  "order.adjustment_removed": "Adjustment taken off",
};

export function syncLabel(online: boolean, queued: number, failed: number, syncing: boolean): string {
  if (failed > 0) return `${failed} need${failed === 1 ? "s" : ""} attention`;
  if (!online) return queued > 0 ? `Offline · ${queued} saved` : "Offline";
  if (queued > 0) return syncing ? `Syncing ${queued}…` : `${queued} to sync`;
  return "Online";
}

export function SyncBadge() {
  const sync = useSync();
  const [open, setOpen] = useState(false);
  const failed = sync.failed.length;
  const tone = failed > 0 ? theme.colors.danger : !sync.online ? theme.colors.warn : theme.colors.success;
  return (
    <>
      <Pressable
        onPress={() => setOpen(true)}
        style={[styles.badge, { borderColor: tone }]}
        accessibilityRole="button"
        accessibilityLabel={`Connection: ${syncLabel(sync.online, sync.queued, failed, sync.syncing)}`}
      >
        <View style={[styles.dot, { backgroundColor: tone }]} />
        <Text style={[styles.badgeText, { color: tone }]}>
          {syncLabel(sync.online, sync.queued, failed, sync.syncing)}
        </Text>
      </Pressable>
      {open ? <SyncSheet onClose={() => setOpen(false)} /> : null}
    </>
  );
}

function SyncSheet({ onClose }: { onClose: () => void }) {
  const sync = useSync();
  const [busy, setBusy] = useState(false);

  async function syncNow() {
    setBusy(true);
    try {
      await sync.syncNow();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Sheet title="Sync" onClose={onClose}>
      <Banner kind={sync.online ? "success" : "info"}>
        {sync.online
          ? "Connected to the Void POS server."
          : "Can't reach the server. The till keeps taking orders, cash and card-on-terminal payments, and sends them when the server is back. Discounts, voids, refunds and Stripe cards need the server."}
      </Banner>
      <Muted>
        {sync.queued === 0
          ? "Nothing waiting to sync."
          : `${sync.queued} change${sync.queued === 1 ? "" : "s"} saved on this till, waiting for the server.`}
      </Muted>
      <Button title={busy || sync.syncing ? "Syncing…" : "Sync now"} onPress={() => void syncNow()} loading={busy} />

      {sync.failed.length > 0 ? (
        <View style={{ gap: theme.space(2) }}>
          <Text style={styles.heading}>Refused by the server</Text>
          <Muted>Note these, sort them out at the till or in the back office, then discard them.</Muted>
          {sync.failed.map((e) => (
            <View key={e.event.event_id} style={styles.failed}>
              <View style={{ flex: 1 }}>
                <Text style={styles.failedTitle}>{TYPE_LABELS[e.event.type] ?? e.event.type}</Text>
                <Muted>{e.error ?? e.code ?? "refused"}</Muted>
              </View>
              <Button title="Discard" onPress={() => void sync.discard(e.event.event_id)} variant="ghost" />
            </View>
          ))}
        </View>
      ) : null}

      {sync.notices.length > 0 ? (
        <View style={{ gap: theme.space(2) }}>
          <Text style={styles.heading}>From the last syncs</Text>
          {sync.notices.map((n, i) => (
            <Banner key={i} kind={n.kind === "error" ? "error" : n.kind === "warn" ? "info" : "success"}>
              {n.text}
            </Banner>
          ))}
          <Button title="Clear" onPress={sync.dismissNotices} variant="ghost" />
        </View>
      ) : null}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    borderWidth: 1,
    borderRadius: theme.radius.pill,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  badgeText: { fontSize: 12, fontWeight: "700" },
  heading: { fontSize: 15, fontWeight: "700", color: theme.colors.text },
  failed: {
    flexDirection: "row",
    alignItems: "center",
    gap: theme.space(2),
    borderBottomWidth: 1,
    borderBottomColor: theme.colors.border,
    paddingVertical: theme.space(2),
  },
  failedTitle: { fontSize: 14, fontWeight: "600", color: theme.colors.text },
});
