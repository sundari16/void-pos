"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { ApiError } from "@/lib/api";
import { createAdminApi, errorDetail, type AdminApi } from "@/lib/adminApi";
import {
  clearAdmin,
  isBadToken,
  loadAdmin,
  LOCATION_KEY,
  pickLocation,
  saveLocation,
  saveToken,
  sessionStore,
  signInError,
} from "@/lib/adminSession";
import type { AdminLocation } from "@/lib/adminTypes";

export const SIGNED_OUT_NOTICE = "The server no longer accepts that admin token. Sign in again.";

interface AdminContextValue {
  /** False until the saved sign-in has been checked. */
  ready: boolean;
  token: string | null;
  api: AdminApi | null;
  locations: AdminLocation[];
  location: AdminLocation | null;
  /** Why the admin was last signed out, or why the saved sign-in couldn't be checked. */
  notice: string | null;
  signIn: (token: string) => Promise<void>;
  signOut: (notice?: string) => void;
  chooseLocation: (id: string | null) => void;
  /** Swap in a location the server just returned (after editing venue details). */
  updateLocation: (location: AdminLocation) => void;
  retry: () => void;
}

const AdminContext = createContext<AdminContextValue | null>(null);

export function AdminProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [token, setToken] = useState<string | null>(null);
  const [locations, setLocations] = useState<AdminLocation[]>([]);
  const [locationId, setLocationId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  const signOut = useCallback((reason?: string) => {
    clearAdmin(sessionStore());
    setToken(null);
    setLocations([]);
    setLocationId(null);
    setNotice(reason ?? null);
  }, []);

  // Any 401 from an admin call means the token stopped working: back to sign in.
  const api = useMemo(() => {
    if (!token) return null;
    return createAdminApi(token, async (input, init) => {
      const res = await fetch(input, init);
      if (res.status === 401) signOut(SIGNED_OUT_NOTICE);
      return res;
    });
  }, [token, signOut]);

  useEffect(() => {
    const store = sessionStore();
    const saved = loadAdmin(store);
    if (!saved.token) {
      setReady(true);
      return;
    }
    let active = true;
    createAdminApi(saved.token)
      .locations()
      .then((list) => {
        if (!active) return;
        setToken(saved.token);
        setLocations(list);
        setLocationId(pickLocation(list, saved.locationId));
        setNotice(null);
      })
      .catch((err) => {
        if (!active) return;
        if (err instanceof ApiError && isBadToken(err.status, err.code)) signOut(SIGNED_OUT_NOTICE);
        else setNotice(errorDetail(err));
      })
      .finally(() => active && setReady(true));
    return () => {
      active = false;
    };
  }, [attempt, signOut]);

  const value = useMemo<AdminContextValue>(
    () => ({
      ready,
      token,
      api,
      locations,
      location: locations.find((l) => l.id === locationId) ?? null,
      notice,
      async signIn(raw) {
        const pasted = raw.trim();
        let list: AdminLocation[];
        try {
          list = await createAdminApi(pasted).locations();
        } catch (err) {
          if (err instanceof ApiError && err.status !== 0) {
            throw new Error(signInError(err.status, err.code, err.detail));
          }
          throw new Error(errorDetail(err));
        }
        const store = sessionStore();
        saveToken(store, pasted);
        const picked = pickLocation(list, loadAdmin(store).locationId);
        if (picked) saveLocation(store, picked);
        setToken(pasted);
        setLocations(list);
        setLocationId(picked);
        setNotice(null);
      },
      signOut,
      chooseLocation(id) {
        const store = sessionStore();
        if (id) saveLocation(store, id);
        else store?.removeItem(LOCATION_KEY);
        setLocationId(id);
      },
      updateLocation(next) {
        setLocations((list) => list.map((l) => (l.id === next.id ? next : l)));
      },
      retry() {
        setNotice(null);
        setReady(false);
        setAttempt((n) => n + 1);
      },
    }),
    [ready, token, api, locations, locationId, notice, signOut],
  );

  return <AdminContext.Provider value={value}>{children}</AdminContext.Provider>;
}

export function useAdmin(): AdminContextValue {
  const value = useContext(AdminContext);
  if (!value) throw new Error("useAdmin must be used inside <AdminProvider>");
  return value;
}

/** For screens inside the signed-in shell: the API and the chosen location. */
export function useAdminLocation(): { api: AdminApi; location: AdminLocation } {
  const { api, location } = useAdmin();
  if (!api || !location) throw new Error("useAdminLocation needs a signed-in admin and a location");
  return { api, location };
}
