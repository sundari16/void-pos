// Where the API is. The mobile till calls the backend directly (no same-origin proxy like
// the web till), so it needs an absolute base URL. EXPO_PUBLIC_API_BASE is inlined at build
// time by Expo; without it the default is http://localhost:8000 (the API's dev port).
//
// On a real device or the Android emulator "localhost" is the device itself, not your
// machine — set EXPO_PUBLIC_API_BASE to your machine's LAN address (or 10.0.2.2 on the
// Android emulator). Money is integer cents everywhere; see money.ts.

const BUILD_API_BASE = (process.env.EXPO_PUBLIC_API_BASE ?? "").trim();

export const CURRENCY = "AUD";
export const DEFAULT_API_BASE = "http://localhost:8000";

export function apiBase(override: string = BUILD_API_BASE): string {
  return (override || DEFAULT_API_BASE).replace(/\/$/, "");
}
