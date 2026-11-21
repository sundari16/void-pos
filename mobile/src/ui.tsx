// Small shared UI primitives, so the screens stay about the POS flow and not about styling.

import type { ReactNode } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type StyleProp,
  type TextInputProps,
  type TextStyle,
  type ViewStyle,
} from "react-native";
import { STATE_COLORS, theme } from "./theme";

type ButtonVariant = "primary" | "outline" | "ghost" | "danger";

export function Button({
  title,
  onPress,
  disabled,
  loading,
  variant = "primary",
  style,
}: {
  title: string;
  onPress: () => void;
  disabled?: boolean;
  loading?: boolean;
  variant?: ButtonVariant;
  style?: StyleProp<ViewStyle>;
}) {
  const off = disabled || loading;
  const palette: Record<ButtonVariant, { bg: string; fg: string; border: string }> = {
    primary: { bg: theme.colors.accent, fg: theme.colors.accentText, border: theme.colors.accent },
    outline: { bg: theme.colors.surface, fg: theme.colors.accent, border: theme.colors.accent },
    ghost: { bg: "transparent", fg: theme.colors.muted, border: "transparent" },
    danger: { bg: theme.colors.surface, fg: theme.colors.danger, border: theme.colors.danger },
  };
  const c = palette[variant];
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={off}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: c.bg, borderColor: c.border, opacity: off ? 0.4 : pressed ? 0.9 : 1 },
        style,
      ]}
    >
      {loading ? (
        <ActivityIndicator color={c.fg} />
      ) : (
        <Text style={[styles.buttonText, { color: c.fg }]}>{title}</Text>
      )}
    </Pressable>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Badge({ state }: { state: string }) {
  const c = STATE_COLORS[state] ?? STATE_COLORS.open;
  return (
    <View style={[styles.badge, { backgroundColor: c.bg }]}>
      <Text style={[styles.badgeText, { color: c.fg }]}>{state}</Text>
    </View>
  );
}

export function Chip({
  label,
  selected,
  disabled,
  onPress,
}: {
  label: string;
  selected?: boolean;
  disabled?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ selected, disabled }}
      onPress={onPress}
      disabled={disabled}
      style={[
        styles.chip,
        selected && { backgroundColor: theme.colors.accent, borderColor: theme.colors.accent },
        disabled && { opacity: 0.4 },
      ]}
    >
      <Text style={[styles.chipText, selected && { color: theme.colors.accentText }]}>{label}</Text>
    </Pressable>
  );
}

export function Banner({ kind, children }: { kind: "error" | "info" | "success"; children: ReactNode }) {
  const bg = kind === "error" ? "#f8e3df" : kind === "success" ? "#dcefe2" : "#eef2f8";
  const fg = kind === "error" ? theme.colors.danger : kind === "success" ? theme.colors.success : theme.colors.muted;
  return (
    <View style={[styles.banner, { backgroundColor: bg }]}>
      <Text style={{ color: fg, fontSize: 13 }}>{children}</Text>
    </View>
  );
}

export function Muted({ children, style }: { children: ReactNode; style?: StyleProp<TextStyle> }) {
  return <Text style={[{ color: theme.colors.muted, fontSize: 13 }, style]}>{children}</Text>;
}

/** A bottom sheet over the till: a title, a close button, and its content. */
export function Sheet({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Modal visible animationType="slide" transparent onRequestClose={onClose}>
      <KeyboardAvoidingView style={styles.backdrop} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <Pressable style={{ flex: 1 }} onPress={onClose} accessibilityLabel="Close" />
        <View style={styles.sheet} accessibilityLabel={title}>
          <View style={styles.sheetHeader}>
            <Text style={styles.sheetTitle}>{title}</Text>
            <Pressable onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" hitSlop={10}>
              <Text style={styles.sheetClose}>×</Text>
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.sheetBody} keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

/** A card over the current view (not a native modal, so it can follow a sheet closing). */
export function Overlay({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <View style={[StyleSheet.absoluteFill, styles.overlay]}>
      <Pressable style={StyleSheet.absoluteFill} onPress={onClose} accessibilityLabel="Close" />
      <View style={styles.overlayCard} accessibilityLabel={title}>
        <Text style={styles.sheetTitle}>{title}</Text>
        {children}
      </View>
    </View>
  );
}

export function Input({ style, ...props }: TextInputProps) {
  return <TextInput placeholderTextColor={theme.colors.muted} style={[styles.input, style]} {...props} />;
}

export function ErrorText({ children }: { children: ReactNode }) {
  return <Text style={{ color: theme.colors.danger, fontSize: 13 }}>{children}</Text>;
}

const styles = StyleSheet.create({
  backdrop: { flex: 1, backgroundColor: "rgba(0,0,0,0.35)" },
  overlay: { backgroundColor: "rgba(0,0,0,0.35)", justifyContent: "center", padding: theme.space(6) },
  overlayCard: {
    backgroundColor: theme.colors.surface,
    borderRadius: theme.radius.lg,
    padding: theme.space(4),
    gap: theme.space(3),
  },
  sheet: {
    maxHeight: "88%",
    backgroundColor: theme.colors.surface,
    borderTopLeftRadius: theme.radius.lg,
    borderTopRightRadius: theme.radius.lg,
  },
  sheetHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: theme.space(4),
    paddingTop: theme.space(4),
    paddingBottom: theme.space(2),
  },
  sheetTitle: { fontSize: 18, fontWeight: "700", color: theme.colors.text, flex: 1 },
  sheetClose: { fontSize: 28, color: theme.colors.muted, paddingHorizontal: theme.space(2) },
  sheetBody: { padding: theme.space(4), paddingTop: theme.space(2), gap: theme.space(3) },
  input: {
    borderWidth: 1,
    borderColor: theme.colors.border,
    borderRadius: theme.radius.sm,
    backgroundColor: theme.colors.bg,
    paddingHorizontal: theme.space(3),
    paddingVertical: theme.space(3),
    fontSize: 15,
    color: theme.colors.text,
  },
  button: {
    minHeight: 48,
    paddingHorizontal: theme.space(4),
    borderRadius: theme.radius.md,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonText: { fontSize: 16, fontWeight: "600" },
  card: {
    backgroundColor: theme.colors.surface,
    borderRadius: theme.radius.lg,
    borderWidth: 1,
    borderColor: theme.colors.border,
    padding: theme.space(4),
  },
  badge: { borderRadius: theme.radius.pill, paddingHorizontal: 10, paddingVertical: 4 },
  badgeText: { fontSize: 12, fontWeight: "700", textTransform: "capitalize" },
  chip: {
    borderRadius: theme.radius.pill,
    borderWidth: 1,
    borderColor: theme.colors.border,
    backgroundColor: theme.colors.surface,
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
  chipText: { fontSize: 14, color: theme.colors.text, fontWeight: "500" },
  banner: { borderRadius: theme.radius.md, padding: theme.space(3) },
});
