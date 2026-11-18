// How the till takes payment, for the hybrid model: cash, or a card taken on the venue's own
// terminal and recorded here with the terminal's receipt or approval number (method
// "external"). Integrated card payments (Stripe) come later through the same seam.

export type PayMethod = "cash" | "external";

const LABELS: Record<string, string> = {
  cash: "Cash",
  external: "Card on terminal",
  stripe: "Card (Stripe)",
};

/** A person-facing name for a payment provider as the API reports it. */
export function providerLabel(provider: string): string {
  return LABELS[provider] ?? provider;
}

export const MAX_TERMINAL_REF = 200;

/** The terminal reference as it will be sent, or an error to show the cashier. */
export function terminalRef(input: string): { ref: string } | { error: string } {
  const ref = input.trim();
  if (!ref) return { error: "Enter the receipt or approval number from the terminal." };
  if (ref.length > MAX_TERMINAL_REF) return { error: "That reference is too long." };
  return { ref };
}
