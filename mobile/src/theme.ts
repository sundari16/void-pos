// Visual tokens for the till. One light theme, sized for a tablet or phone held in a busy
// venue: big tap targets, high contrast, AUD amounts in a tabular feel.

export const theme = {
  colors: {
    bg: "#f4f4f2",
    surface: "#ffffff",
    border: "#e3e3df",
    text: "#1b1b19",
    muted: "#70706a",
    accent: "#1f6feb",
    accentText: "#ffffff",
    danger: "#c0392b",
    success: "#1f8a4c",
    warn: "#b7791f",
    chipBg: "#ececea",
  },
  radius: { sm: 8, md: 12, lg: 16, pill: 999 },
  space: (n: number): number => n * 4,
} as const;

/** Colours for an order's state badge. */
export const STATE_COLORS: Record<string, { bg: string; fg: string }> = {
  open: { bg: "#ececea", fg: "#44443f" },
  sent: { bg: "#fbeccb", fg: "#8a5a05" },
  paid: { bg: "#d5efdd", fg: "#136c39" },
  voided: { bg: "#f6d7d2", fg: "#8e2a1c" },
  refunded: { bg: "#f6d7d2", fg: "#8e2a1c" },
};
