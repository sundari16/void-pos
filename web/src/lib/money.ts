// Money is integer cents end to end. These helpers are for *display only* —
// never store or compute order amounts in the formatted form.

const formatters = new Map<string, Intl.NumberFormat>();

function formatterFor(currency: string): Intl.NumberFormat {
  let fmt = formatters.get(currency);
  if (!fmt) {
    fmt = new Intl.NumberFormat("en-AU", { style: "currency", currency });
    formatters.set(currency, fmt);
  }
  return fmt;
}

/** 1850 -> "$18.50" (AUD). */
export function formatCents(cents: number, currency = "AUD"): string {
  return formatterFor(currency).format(cents / 100);
}

/** Signed delta for modifiers: 150 -> "+$1.50", -50 -> "-$0.50", 0 -> "". */
export function formatDelta(cents: number, currency = "AUD"): string {
  if (cents === 0) return "";
  const sign = cents > 0 ? "+" : "-";
  return `${sign}${formatCents(Math.abs(cents), currency)}`;
}
