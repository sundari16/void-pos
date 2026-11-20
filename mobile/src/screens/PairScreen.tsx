// Shown until this device is paired as a till: redeem a one-time pairing code for the till's
// bearer token, location and counters. Mirrors web/src/components/PairTill.tsx.

import { useState } from "react";
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
} from "react-native";
import { api } from "../lib/api";
import { friendlyError } from "../lib/commands";
import { apiBase } from "../lib/config";
import { tillFromPairing } from "../lib/till";
import { useTill } from "../TillContext";
import { theme } from "../theme";
import { Banner, Button, Card, Muted } from "../ui";

export function PairScreen() {
  const { pairWith, notice } = useTill();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    const trimmed = code.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      pairWith(tillFromPairing(await api.pair(trimmed)));
    } catch (err) {
      setError(friendlyError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.fill}
      behavior={Platform.OS === "ios" ? "padding" : undefined}
    >
      <ScrollView contentContainerStyle={styles.center} keyboardShouldPersistTaps="handled">
        <Card style={styles.card}>
          <Text style={styles.title}>Pair this till</Text>
          <Muted style={{ marginTop: 4 }}>
            Enter the pairing code from the venue admin. Each code works once.
          </Muted>

          {notice ? <Banner kind="info">{notice}</Banner> : null}

          <Text style={styles.label}>Pairing code</Text>
          <TextInput
            value={code}
            onChangeText={(t) => setCode(t.toUpperCase())}
            placeholder="K7QM-4XPA"
            placeholderTextColor={theme.colors.muted}
            autoCapitalize="characters"
            autoCorrect={false}
            autoComplete="one-time-code"
            style={styles.input}
            editable={!busy}
            onSubmitEditing={() => void submit()}
            returnKeyType="go"
          />

          {error ? <Banner kind="error">{error}</Banner> : null}

          <Button
            title={busy ? "Pairing…" : "Pair till"}
            onPress={() => void submit()}
            disabled={!code.trim()}
            loading={busy}
          />

          <Muted style={{ fontSize: 12 }}>Server {apiBase()}</Muted>
        </Card>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1 },
  center: { flexGrow: 1, justifyContent: "center", padding: theme.space(4) },
  card: { gap: theme.space(3), maxWidth: 420, width: "100%", alignSelf: "center" },
  title: { fontSize: 20, fontWeight: "700", color: theme.colors.text },
  label: { fontSize: 14, fontWeight: "600", color: theme.colors.text },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(3),
    fontSize: 20,
    letterSpacing: 4,
    color: theme.colors.text,
    fontFamily: Platform.select({ ios: "Menlo", android: "monospace", default: "monospace" }),
  },
});
