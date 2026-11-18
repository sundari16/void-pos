// Where the API is. In a venue install the API runs on the same machine as this web app,
// on port 8000, so one build works at every venue. NEXT_PUBLIC_API_BASE overrides that
// (it is fixed at build time). Money is integer cents everywhere; see money.ts.

const BUILD_API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim();

export const CURRENCY = "AUD";
export const API_PORT = 8000;

interface PageLocation {
  protocol: string;
  hostname: string;
}

export function apiBase(page?: PageLocation): string {
  if (BUILD_API_BASE) return BUILD_API_BASE.replace(/\/$/, "");
  const loc = page ?? (typeof window !== "undefined" ? window.location : undefined);
  if (!loc) return `http://localhost:${API_PORT}`;
  return `${loc.protocol}//${loc.hostname}:${API_PORT}`;
}
