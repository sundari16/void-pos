// Where the API is. By default the till calls `/api` on the origin that served it, and the
// web server proxies that to the API (see next.config.ts), so one build works at every
// venue. NEXT_PUBLIC_API_BASE points the till straight at an API instead (fixed at build
// time; the API must then allow this origin via VOIDPOS_CORS_ORIGINS). Money is integer
// cents everywhere; see money.ts.

const BUILD_API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim();

export const CURRENCY = "AUD";
export const SAME_ORIGIN_API = "/api";

export function apiBase(override: string = BUILD_API_BASE): string {
  return override ? override.replace(/\/$/, "") : SAME_ORIGIN_API;
}
