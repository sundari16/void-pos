// This till's session: pairing token, location, and the seq / order-number counters, held in
// memory and persisted to the device (storage.ts). Mirrors the web till's TillProvider
// (web/src/components/TillProvider.tsx) so the two tills take counters and recover from a
// revoked pairing the same way. Counters are taken synchronously through a ref; persistence
// is fire-and-forget, so a crash mid-request can skip a number but never reuse one.

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
import { ApiError } from "./lib/api";
import { clearTill, loadTill, saveTill } from "./lib/storage";
import {
  refreshTill,
  resyncOrderNumber,
  resyncSeq,
  takeOrderNumber,
  takeSeq,
  type Till,
} from "./lib/till";
import type { CommandIn } from "./lib/types";
import { ulid } from "./lib/ulid";
import { api } from "./net";
import { loadServer } from "./server";

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
  /** Move the counters past what the outbox already holds (never back). */
  catchUp: (lastSeq: number, nextOrderNumber: number) => void;
  /** The till as it is right now (state can lag a render behind). */
  peek: () => Till | null;
}

const TillContext = createContext<TillContextValue | null>(null);

export function TillProvider({ children }: { children: ReactNode }) {
  const [till, setTill] = useState<Till | null>(null);
  const [ready, setReady] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const current = useRef<Till | null>(null);

  const commit = useCallback((next: Till | null) => {
    current.current = next;
    if (next) void saveTill(next);
    else void clearTill();
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
    let active = true;
    void (async () => {
      await loadServer();
      const saved = await loadTill();
      if (!active) return;
      current.current = saved;
      setTill(saved);
      setReady(true);
      if (!saved) return;
      // Catch up with the server: counters may have moved on, or the till may be revoked.
      try {
        const session = await api.me(saved.token);
        if (active && current.current?.token === saved.token) {
          commit(refreshTill(current.current, session));
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) unpair(REVOKED_NOTICE);
        // Anything else (server down, offline): keep working from the saved state.
      }
    })();
    return () => {
      active = false;
    };
  }, [commit, unpair]);

  const value = useMemo<TillContextValue>(() => {
    function paired(): Till {
      if (!current.current) throw new Error("This till isn't paired.");
      return current.current;
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
      catchUp(lastSeq, nextOrderNumber) {
        const now = current.current;
        if (!now) return;
        const next = resyncOrderNumber(resyncSeq(now, lastSeq), nextOrderNumber);
        if (next.nextSeq !== now.nextSeq || next.nextOrderNumber !== now.nextOrderNumber) commit(next);
      },
      peek: () => current.current,
    };
  }, [till, ready, notice, commit, unpair]);

  return <TillContext.Provider value={value}>{children}</TillContext.Provider>;
}

export function useTill(): TillContextValue {
  const value = useContext(TillContext);
  if (!value) throw new Error("useTill must be used inside <TillProvider>");
  return value;
}
