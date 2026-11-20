// The shift closeout for a business date (default: today in the location's timezone): money
// taken, sales and GST, what's still owing, order counts, payments by provider, revenue by
// class. Read-only. Mirrors web/src/app/closeout/page.tsx against GET /reports/{loc}/closeout.

import { useCallback, useEffect, useState } from "react";
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
import { api, ApiError } from "../lib/api";
import { friendlyError } from "../lib/commands";
import { formatCents } from "../lib/money";
import { providerLabel } from "../lib/payments";
import type { Closeout } from "../lib/types";
import { REVOKED_NOTICE, useTill } from "../TillContext";
import { theme } from "../theme";
import { Banner, Card, Muted } from "../ui";

export function CloseoutScreen() {
  const { till, unpair } = useTill();
  const [data, setData] = useState<Closeout | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (!till) return;
    setLoading(true);
    try {
      setData(await api.closeout(till.token, till.locationId));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
      else setError(friendlyError(err));
    } finally {
      setLoading(false);
    }
  }, [till, unpair]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <ScrollView
      contentContainerStyle={styles.scroll}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={() => void load()} />}
    >
      <Text style={styles.title}>Shift closeout</Text>
      {data ? <Muted>{data.business_date}</Muted> : null}
      {error ? <Banner kind="error">{error}</Banner> : null}
      {!data && !error ? <Muted>Loading…</Muted> : null}

      {data ? (
        <>
          <Card style={styles.card}>
            <Stat label="Gross sales" value={formatCents(data.gross_sales_cents)} big />
            <Stat label="Net sales (ex refunds)" value={formatCents(data.net_sales_cents)} />
            <Stat label="GST" value={formatCents(data.gst_cents)} />
            <Stat label="Sales ex GST" value={formatCents(data.sales_ex_gst_cents)} />
            <Stat label="Tips" value={formatCents(data.tips_cents)} />
            <Stat label="Discounts" value={formatCents(data.discounts_cents)} />
            <Stat label="Surcharges" value={formatCents(data.surcharges_cents)} />
            <Stat label="Refunds" value={formatCents(data.refunds_cents)} />
            <Stat label="Still owing (open)" value={formatCents(data.open_balance_cents)} />
          </Card>

          <Text style={styles.section}>Orders</Text>
          <Card style={styles.card}>
            <Stat label="Total" value={String(data.orders.total)} />
            <Stat label="Open" value={String(data.orders.open)} />
            <Stat label="Sent" value={String(data.orders.sent)} />
            <Stat label="Paid" value={String(data.orders.paid)} />
            <Stat label="Voided" value={String(data.orders.voided)} />
            <Stat label="Refunded" value={String(data.orders.refunded)} />
          </Card>

          <Text style={styles.section}>Payments taken</Text>
          <Card style={styles.card}>
            {Object.keys(data.payments_by_provider).length === 0 ? (
              <Muted>No payments yet.</Muted>
            ) : (
              Object.entries(data.payments_by_provider).map(([provider, cents]) => (
                <Stat key={provider} label={providerLabel(provider)} value={formatCents(cents)} />
              ))
            )}
          </Card>

          {Object.keys(data.revenue_by_class_cents).length > 0 ? (
            <>
              <Text style={styles.section}>Revenue by class</Text>
              <Card style={styles.card}>
                {Object.entries(data.revenue_by_class_cents).map(([cls, cents]) => (
                  <Stat key={cls} label={cls} value={formatCents(cents)} />
                ))}
              </Card>
            </>
          ) : null}
        </>
      ) : null}
    </ScrollView>
  );
}

function Stat({ label, value, big }: { label: string; value: string; big?: boolean }) {
  return (
    <View style={styles.stat}>
      <Muted style={big ? { fontSize: 15 } : undefined}>{label}</Muted>
      <Text style={[styles.value, big && styles.valueBig]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  scroll: { padding: theme.space(4), gap: theme.space(3) },
  title: { fontSize: 20, fontWeight: "700", color: theme.colors.text },
  section: {
    marginTop: theme.space(2),
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
    letterSpacing: 0.5,
    color: theme.colors.muted,
  },
  card: { gap: theme.space(1) },
  stat: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    paddingVertical: theme.space(1),
  },
  value: { fontSize: 15, fontWeight: "600", color: theme.colors.text },
  valueBig: { fontSize: 22, fontWeight: "800" },
});
