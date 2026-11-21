// Offline-first sync for the paired till: opens the on-device store, keeps track of whether
// the server can be reached, and runs the sync loop (pull the other tills' changes, push the
// outbox) every few seconds, sooner when the app comes back to the foreground. Screens queue
// changes through `record` when the server is out of reach and keep every server answer
// through `remember`, so the till always has its latest copy of an order to carry on with.

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
import { AppState } from "react-native";
import type { OrderResponse, SyncEvent } from "./lib/types";
import { api } from "./net";
import { businessDate, seedOccurred, type TillIdentity } from "./offline/events";
import { openSqliteStore } from "./offline/sqliteStore";
import { entryOrderId, MemoryStore, type OutboxEntry, type TillStore } from "./offline/store";
import { queuedCounters, TillSync, type Notice } from "./offline/sync";
import { REVOKED_NOTICE, useTill } from "./TillContext";

const ONLINE_EVERY_MS = 20_000;
const OFFLINE_EVERY_MS = 8_000;
const MAX_NOTICES = 20;

interface SyncValue {
  /** The store is open and the till's counters are past everything queued. */
  ready: boolean;
  /** The server answered the last time the till tried it (true until it's tried). */
  online: boolean;
  syncing: boolean;
  /** Changes waiting for the server. */
  queued: number;
  /** Orders with changes waiting for the server. */
  queuedOrders: Set<string>;
  /** Changes the server refused, kept until the cashier discards them. */
  failed: OutboxEntry[];
  notices: Notice[];
  identity: TillIdentity | null;
  /** A request just failed to reach the server: work offline and try again soon. */
  wentOffline: () => void;
  record: (
    kind: OutboxEntry["kind"],
    orderId: string | null,
    fallback: OrderResponse | null,
    build: (order: OrderResponse | null) => SyncEvent,
  ) => Promise<OrderResponse>;
  remember: (order: OrderResponse) => Promise<OrderResponse>;
  hasQueued: (orderId: string) => Promise<boolean>;
  /** Sync now; resolves once done (whether or not the server answered). */
  syncNow: () => Promise<void>;
  discard: (eventId: string) => Promise<void>;
  dismissNotices: () => void;
  localOpenOrders: () => Promise<OrderResponse[]>;
  localOrder: (orderId: string) => Promise<OrderResponse | null>;
  /** Hear about orders the sync changed (refreshed from the server, or pulled). */
  subscribe: (listener: (orderIds: string[]) => void) => () => void;
}

const SyncContext = createContext<SyncValue | null>(null);

let opened: Promise<TillStore> | null = null;
function openStore(): Promise<TillStore> {
  // One store for the app's life; if SQLite can't open, work from memory.
  opened ??= openSqliteStore().catch(() => new MemoryStore());
  return opened;
}

export function SyncProvider({ children }: { children: ReactNode }) {
  const { till, peek, seqAfter, orderNumberAfter, catchUp, unpair } = useTill();
  const [engine, setEngine] = useState<TillSync | null>(null);
  const [online, setOnline] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [entries, setEntries] = useState<OutboxEntry[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const listeners = useRef(new Set<(ids: string[]) => void>());
  const wake = useRef<() => void>(() => undefined);

  // Keep the latest callbacks for the engine without rebuilding it each render.
  const deps = useRef({ peek, seqAfter, orderNumberAfter, catchUp, unpair });
  useEffect(() => {
    deps.current = { peek, seqAfter, orderNumberAfter, catchUp, unpair };
  });

  const deviceId = till?.deviceId;
  useEffect(() => {
    if (!deviceId) return;
    let active = true;
    void (async () => {
      const store = await openStore();
      // A store left by another pairing belongs to another device's events: start afresh.
      if ((await store.meta("device_id")) !== deviceId) {
        await store.clear();
        await store.setMeta("device_id", deviceId);
      }
      const sync = new TillSync({
        store,
        api,
        till: () => {
          const t = deps.current.peek();
          return t ? { token: t.token, deviceId: t.deviceId, locationId: t.locationId, timezone: t.timezone } : null;
        },
        seqAfter: (last) => deps.current.seqAfter(last),
        orderNumberAfter: (next) => deps.current.orderNumberAfter(next),
        onUnauthorized: () => deps.current.unpair(REVOKED_NOTICE),
        today: () => businessDate(deps.current.peek()?.timezone ?? "Australia/Brisbane"),
      });
      const all = await store.entries();
      for (const entry of all) seedOccurred(entry.event.occurred_at);
      const { lastSeq, lastOrderNumber } = queuedCounters(all);
      deps.current.catchUp(lastSeq, lastOrderNumber + 1);
      await sync.prune();
      if (!active) return;
      setEntries(all);
      setEngine(sync);
    })();
    return () => {
      active = false;
      setEngine(null);
    };
  }, [deviceId]);

  const refresh = useCallback(async (sync: TillSync) => {
    const next = await sync.store.entries();
    // Keep the same array when nothing changed, so what depends on it doesn't rerun.
    setEntries((prev) => (sameEntries(prev, next) ? prev : next));
  }, []);

  const tell = useCallback((ids: string[]) => {
    if (ids.length) for (const listener of listeners.current) listener(ids);
  }, []);

  const runSync = useCallback(
    async (sync: TillSync): Promise<boolean> => {
      setSyncing(true);
      try {
        // Pulling is the reachability check too: it always asks the server.
        const pulled = await sync.pull();
        setOnline(pulled.reached);
        tell(pulled.touched);
        if (!pulled.reached) return false;
        const drained = await sync.drain();
        if (!drained.reached) setOnline(false);
        if (drained.notices.length) setNotices((n) => [...drained.notices, ...n].slice(0, MAX_NOTICES));
        tell(drained.touched);
        return drained.reached;
      } finally {
        setSyncing(false);
        await refresh(sync);
      }
    },
    [refresh, tell],
  );

  // The loop: often while offline (to notice the server coming back), less often online.
  useEffect(() => {
    if (!engine) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const schedule = (ms: number) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void tick(), ms);
    };
    async function tick() {
      if (stopped) return;
      const reached = await runSync(engine!);
      if (!stopped) schedule(reached ? ONLINE_EVERY_MS : OFFLINE_EVERY_MS);
    }
    wake.current = () => schedule(0);
    void tick();
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") schedule(0);
    });
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      sub.remove();
      wake.current = () => undefined;
    };
  }, [engine, runSync]);

  const queuedOrders = useMemo(
    () => new Set(entries.filter((e) => e.status === "pending").map((e) => entryOrderId(e) ?? "")),
    [entries],
  );
  const tillDevice = till?.deviceId;
  const tillLocation = till?.locationId;
  const tillZone = till?.timezone;
  const identity = useMemo<TillIdentity | null>(
    () =>
      tillDevice && tillLocation && tillZone
        ? { deviceId: tillDevice, locationId: tillLocation, timezone: tillZone }
        : null,
    [tillDevice, tillLocation, tillZone],
  );

  const value = useMemo<SyncValue>(() => {
    const pending = entries.filter((e) => e.status === "pending");
    const need = (): TillSync => {
      if (!engine) throw new Error("The till's store isn't open yet.");
      return engine;
    };
    return {
      ready: engine !== null,
      online,
      syncing,
      queued: pending.length,
      queuedOrders,
      failed: entries.filter((e) => e.status === "failed"),
      notices,
      identity,
      wentOffline() {
        setOnline(false);
        setTimeout(() => wake.current(), OFFLINE_EVERY_MS);
      },
      async record(kind, orderId, fallback, build) {
        const sync = need();
        const view = await sync.record(kind, orderId, fallback, build);
        await refresh(sync);
        // Online but queued behind earlier changes: push now rather than at the next tick.
        if (online) wake.current();
        return view;
      },
      remember: (order) => (engine ? engine.remember(order) : Promise.resolve(order)),
      async hasQueued(orderId) {
        return engine ? (await engine.pending(orderId)).length > 0 : false;
      },
      async syncNow() {
        if (engine) await runSync(engine);
      },
      async discard(eventId) {
        const sync = need();
        await sync.discard(eventId);
        await refresh(sync);
      },
      dismissNotices: () => setNotices([]),
      localOpenOrders: () => (engine ? engine.openOrders() : Promise.resolve([])),
      localOrder: (orderId) => (engine ? engine.store.order(orderId) : Promise.resolve(null)),
      subscribe(listener) {
        listeners.current.add(listener);
        return () => {
          listeners.current.delete(listener);
        };
      },
    };
  }, [engine, entries, queuedOrders, identity, notices, online, syncing, refresh, runSync]);

  return <SyncContext.Provider value={value}>{children}</SyncContext.Provider>;
}

function sameEntries(a: OutboxEntry[], b: OutboxEntry[]): boolean {
  return (
    a.length === b.length &&
    a.every((e, i) => {
      const f = b[i];
      return e.event.event_id === f.event.event_id && e.event.seq === f.event.seq && e.status === f.status && e.attempts === f.attempts;
    })
  );
}

export function useSync(): SyncValue {
  const value = useContext(SyncContext);
  if (!value) throw new Error("useSync must be used inside <SyncProvider>");
  return value;
}
