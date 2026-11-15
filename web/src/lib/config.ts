// Runtime configuration, read from NEXT_PUBLIC_* env vars at build time.
// Keep money in integer cents everywhere; only format for display (see money.ts).

export const config = {
  apiBase: (process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000").replace(/\/$/, ""),
  locationId: process.env.NEXT_PUBLIC_LOCATION_ID ?? "",
  deviceId: process.env.NEXT_PUBLIC_DEVICE_ID ?? "",
  orderNumberStart: Number(process.env.NEXT_PUBLIC_ORDER_NUMBER_START ?? "1000"),
  currency: "AUD",
} as const;

/** True once the till knows which location + device it is ringing orders against. */
export function isConfigured(): boolean {
  return Boolean(config.locationId && config.deviceId);
}
