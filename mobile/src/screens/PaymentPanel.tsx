// Take the balance, or part of it: a typed amount or an even share of a split bill, with an
// optional tip, as cash, a card on the venue's terminal, or a card on this screen through
// Stripe. After each payment the amount moves on to what's left (or the next share).
// Mirrors web/src/components/PaymentPanel.tsx.

import { useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { SPLITS, TIPS, payAmount, tipAmount } from "../flows";
import { formatCents } from "../lib/money";
import { centsToInput, evenSplit, terminalRef, type PayMethod } from "../lib/payments";
import { percentText } from "../lib/placed";
import { theme } from "../theme";
import { Button, Chip, ErrorText, Input, Muted } from "../ui";

interface Split {
  shares: number[];
  index: number;
}

export function PaymentPanel({
  balanceCents,
  busy,
  retryMethod,
  offline,
  onPay,
  onCard,
}: {
  balanceCents: number;
  busy: boolean;
  /** An unconfirmed earlier payment: the only way on is to resend it as it was. */
  retryMethod: PayMethod | null;
  /** Stripe needs the server; cash and the terminal don't. */
  offline: boolean;
  onPay: (method: PayMethod, amountCents: number, externalRef?: string, tipCents?: number) => void;
  /** A card through Stripe on this screen; absent when Stripe isn't set up. */
  onCard?: (amountCents: number, tipCents: number) => void;
}) {
  const [split, setSplit] = useState<Split | null>(null);
  const [amount, setAmount] = useState(centsToInput(balanceCents));
  const [lastBalance, setLastBalance] = useState(balanceCents);
  const [cardOpen, setCardOpen] = useState(false);
  const [cardRef, setCardRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tipBp, setTipBp] = useState<number | null>(0);
  const [tipText, setTipText] = useState("");

  // A payment went through: move on to the next share, or to whatever is left.
  if (balanceCents !== lastBalance) {
    setLastBalance(balanceCents);
    const index = split ? split.index + 1 : 0;
    const nextSplit = split && index < split.shares.length ? { ...split, index } : null;
    setSplit(nextSplit);
    setAmount(centsToInput(nextSplit ? Math.min(nextSplit.shares[index], balanceCents) : balanceCents));
    setCardOpen(false);
    setCardRef("");
    setError(null);
    setTipBp(0);
    setTipText("");
  }

  function chooseSplit(ways: number | null) {
    setError(null);
    if (ways === null) {
      setSplit(null);
      setAmount(centsToInput(balanceCents));
      return;
    }
    const shares = evenSplit(balanceCents, ways);
    setSplit({ shares, index: 0 });
    setAmount(centsToInput(shares[0]));
  }

  /** The amount and tip, or null after showing why not. */
  function checked(): { cents: number; tip: number } | null {
    const paid = payAmount(amount, balanceCents);
    if ("error" in paid) {
      setError(paid.error);
      return null;
    }
    const tip = tipAmount(paid.cents, tipBp, tipText);
    if ("error" in tip) {
      setError(tip.error);
      return null;
    }
    setError(null);
    return { cents: paid.cents, tip: tip.cents };
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
    <View style={styles.box}>
      <View style={styles.header}>
        <Text style={styles.label}>{split ? `Share ${split.index + 1} of ${split.shares.length}` : "Amount"}</Text>
        <Muted>Owing {formatCents(balanceCents)}</Muted>
      </View>
      <View style={styles.amountField}>
        <Text style={styles.dollar}>$</Text>
        <Input
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
      <View style={styles.chips}>
        <Chip label="Whole" selected={split === null} onPress={() => chooseSplit(null)} />
        {SPLITS.map((ways) => (
          <Chip
            key={ways}
            label={`Split ${ways}`}
            selected={split?.shares.length === ways}
            disabled={balanceCents < ways}
            onPress={() => chooseSplit(ways)}
          />
        ))}
      </View>
      <View style={styles.chips}>
        <Text style={styles.small}>Tip</Text>
        <Chip label="None" selected={tipBp === 0} onPress={() => setTipBp(0)} />
        {TIPS.map((bp) => (
          <Chip key={bp} label={percentText(bp)} selected={tipBp === bp} onPress={() => setTipBp(bp)} />
        ))}
        <Chip label="$" selected={tipBp === null} onPress={() => setTipBp(null)} />
        {tipBp === null ? (
          <Input
            value={tipText}
            onChangeText={setTipText}
            keyboardType="decimal-pad"
            placeholder="0.00"
            accessibilityLabel="Tip amount"
            style={styles.tipInput}
          />
        ) : null}
      </View>
      {error ? <ErrorText>{error}</ErrorText> : null}

      {cardOpen ? (
        <View style={{ gap: theme.space(2) }}>
          <Text style={styles.label}>Terminal receipt or approval number</Text>
          <Input
            value={cardRef}
            onChangeText={setCardRef}
            autoCapitalize="characters"
            autoCorrect={false}
            accessibilityLabel="Terminal reference"
            style={{ fontFamily: "monospace" }}
          />
          <View style={styles.buttons}>
            <Button
              title={busy ? "Recording…" : "Record card payment"}
              onPress={() => {
                const ok = checked();
                if (!ok) return;
                const ref = terminalRef(cardRef);
                if ("error" in ref) return setError(ref.error);
                onPay("external", ok.cents, ref.ref, ok.tip);
              }}
              disabled={!cardRef.trim()}
              loading={busy}
              style={styles.flex}
            />
            <Button
              title="Cancel"
              onPress={() => {
                setCardOpen(false);
                setCardRef("");
              }}
              variant="outline"
              style={styles.flex}
            />
          </View>
        </View>
      ) : (
        <View style={{ gap: theme.space(2) }}>
          <View style={styles.buttons}>
            <Button
              title={busy ? "Paying…" : "Pay cash"}
              onPress={() => {
                const ok = checked();
                if (ok) onPay("cash", ok.cents, undefined, ok.tip);
              }}
              loading={busy}
              style={styles.flex}
            />
            <Button
              title="Card on terminal"
              onPress={() => checked() && setCardOpen(true)}
              variant="outline"
              style={styles.flex}
            />
          </View>
          {onCard ? (
            <Button
              title={offline ? "Card on screen (needs the server)" : "Card on screen"}
              onPress={() => {
                const ok = checked();
                if (ok) onCard(ok.cents, ok.tip);
              }}
              disabled={busy || offline}
              variant="outline"
            />
          ) : null}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  box: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.md,
    padding: theme.space(3),
    gap: theme.space(2),
  },
  header: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between" },
  label: { fontSize: 14, fontWeight: "600", color: theme.colors.text },
  small: { fontSize: 13, color: theme.colors.muted },
  amountField: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.bg,
    paddingLeft: theme.space(3),
  },
  dollar: { color: theme.colors.muted, fontSize: 16 },
  amountInput: { flex: 1, borderWidth: 0, fontSize: 16, fontWeight: "600" },
  chips: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: theme.space(2) },
  tipInput: { minWidth: 80, paddingVertical: theme.space(2) },
  buttons: { flexDirection: "row", gap: theme.space(2) },
});
