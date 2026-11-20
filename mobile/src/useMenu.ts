// Loads this location's till menu and keeps it fresh, with the location-local clock the menu
// rules compare against. Mirrors the web till's menu loading (web/src/app/page.tsx).

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./lib/api";
import { friendlyError } from "./lib/commands";
import { localClock } from "./lib/menu";
import type { TillMenu } from "./lib/types";

const MENU_REFRESH_MS = 5 * 60_000;
const CLOCK_TICK_MS = 30_000;

export function useMenu(
  token: string | undefined,
  locationId: string | undefined,
  onUnauthorized: () => void,
) {
  const [menu, setMenu] = useState<TillMenu | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState("00:00:00");
  // Keep the latest callback without making it a dependency of the load effect (it is a fresh
  // closure each render); update the ref in an effect, never during render.
  const onUnauth = useRef(onUnauthorized);
  useEffect(() => {
    onUnauth.current = onUnauthorized;
  });

  const reload = useCallback(async () => {
    if (!token || !locationId) return;
    try {
      const fresh = await api.tillMenu(token, locationId);
      setMenu(fresh);
      setNow(localClock(fresh.timezone));
      setError(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) onUnauth.current();
      else setError(friendlyError(err));
    }
  }, [token, locationId]);

  // Load the menu, then keep it fresh: an item switched off greys out within minutes.
  useEffect(() => {
    void reload();
    const id = setInterval(() => void reload(), MENU_REFRESH_MS);
    return () => clearInterval(id);
  }, [reload]);

  // The clock moves items in and out of their daily windows.
  const timezone = menu?.timezone;
  useEffect(() => {
    if (!timezone) return;
    const id = setInterval(() => setNow(localClock(timezone)), CLOCK_TICK_MS);
    return () => clearInterval(id);
  }, [timezone]);

  return { menu, error, now, reload };
}
