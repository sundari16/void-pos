// Money is integer cents end to end. These helpers are for *display only* — never store or
// compute order amounts in the formatted form.
//
// Formatted by hand rather than through Intl.NumberFormat: React Native's Hermes engine
// doesn't ship currency formatting consistently across iOS and Android, so a plain formatter
// keeps "$18.50" identical on every device. AUD-first (the project default); any other
// currency is prefixed with its code.

function groupThousands(whole: number): string {
  return whole.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** 1850 -> "$18.50" (AUD); -500 -> "-$5.00". */
export function formatCents(cents: number, currency = "AUD"): string {
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const dollars = groupThousands(Math.floor(abs / 100));
  const frac = String(abs % 100).padStart(2, "0");
  const body = `$${dollars}.${frac}`;
  const withCode = currency === "AUD" ? body : `${currency} ${body}`;
  return negative ? `-${withCode}` : withCode;
}

/** Signed delta for modifiers: 150 -> "+$1.50", -50 -> "-$0.50", 0 -> "". */
export function formatDelta(cents: number, currency = "AUD"): string {
  if (cents === 0) return "";
  const sign = cents > 0 ? "+" : "-";
  return `${sign}${formatCents(Math.abs(cents), currency)}`;
}
