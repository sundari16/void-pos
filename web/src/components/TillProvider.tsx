"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "@/lib/api";
import {
  browserStore,
  clearTill,
  loadTill,
  mergeCounters,
  refreshTill,
  resyncOrderNumber,
  resyncSeq,
  saveTill,
  takeOrderNumber,
  takeSeq,
  type Till,
} from "@/lib/till";
import type { CommandIn } from "@/lib/types";
import { ulid } from "@/lib/ulid";

export const REVOKED_NOTICE = "This till's pairing was revoked or replaced. Pair it again.";

interface TillContextValue {
  till: Till | null;
  /** False until saved state has been read; render nothing till-specific before then. */
  ready: boolean;
  /** Why the till was last unpaired, shown on the pairing screen. */
  notice: string | null;
  pairWith: (till: Till) => void;
  unpair: (reason?: string) => void;
  /** A fresh event id and the next seq, saved before the request goes out. */
  command: () => CommandIn;
  /** The next order number, saved before use. Throws RangeExhausted at the range end. */
  orderNumber: () => number;
  /** Resync after a seq_conflict and return a fresh seq. */
  seqAfter: (lastSeq: number) => number;
  /** Resync after order_number_used and return a fresh order number. */
  orderNumberAfter: (next: number) => number;
}

const TillContext = createContext<TillContextValue | null>(null);

export function TillProvider({ children }: { children: ReactNode }) {
  const [till, setTill] = useState<Till | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  // Counters are taken synchronously inside click handlers, so read and write the latest
  // till through a ref rather than a possibly stale render value.
  const current = useRef<Till | null>(null);

  const commit = useCallback((next: Till | null) => {
    current.current = next;
    const store = browserStore();
    if (store) {
      if (next) saveTill(store, next);
      else clearTill(store);
    }
    setTill(next);
  }, []);

  const unpair = useCallback(
    (reason?: string) => {
      setNotice(reason ?? null);
      commit(null);
    },
    [commit],
  );

  useEffect(() => {
    const saved = loadTill(browserStore());
    current.current = saved;
    setTill(saved);
    setReady(true);
    if (!saved) return;
    // Catch up with the server: counters may have moved on, or the till may be revoked.
    api
      .me(saved.token)
      .then((session) => {
        if (current.current?.token === saved.token) commit(refreshTill(current.current, session));
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
        // Anything else (server down, offline): keep working from the saved state.
      });
  }, [commit, unpair]);

  const value = useMemo<TillContextValue>(() => {
    function paired(): Till {
      if (!current.current) throw new Error("This till isn't paired.");
      // Another tab of this till may have moved the counters on since we last looked.
      return mergeCounters(current.current, loadTill(browserStore()));
    }
    return {
      till,
      ready,
      notice,
      pairWith(next) {
        setNotice(null);
        commit(next);
      },
      unpair,
      command() {
        const [seq, next] = takeSeq(paired());
        commit(next);
        return { event_id: ulid(), seq };
      },
      orderNumber() {
        const [number, next] = takeOrderNumber(paired());
        commit(next);
        return number;
      },
      seqAfter(lastSeq) {
        const [seq, next] = takeSeq(resyncSeq(paired(), lastSeq));
        commit(next);
        return seq;
      },
      orderNumberAfter(nextNumber) {
        const [number, next] = takeOrderNumber(resyncOrderNumber(paired(), nextNumber));
        commit(next);
        return number;
      },
    };
  }, [till, ready, notice, commit, unpair]);

  return <TillContext.Provider value={value}>{children}</TillContext.Provider>;
}

export function useTill(): TillContextValue {
  const value = useContext(TillContext);
  if (!value) throw new Error("useTill must be used inside <TillProvider>");
  return value;
}
