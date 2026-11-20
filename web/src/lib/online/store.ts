// Remembering the customer between visits, per location, as a browser convenience only —
// their id, name and contact, so a returning customer isn't retyping and can see their
// points. Never relied on: the server re-matches by phone/email and is the source of truth.
// Every access is wrapped: private windows and cleared storage must not break the page.

export interface RememberedCustomer {
  customer_id: string;
  name: string;
  phone?: string | null;
  email?: string | null;
  marketing_opt_in: boolean;
}

const KEY = "voidpos.online.customer";

function key(locationId: string): string {
  return `${KEY}.${locationId}`;
}

export function recallCustomer(
  locationId: string,
  storage: Storage | undefined = safeStorage(),
): RememberedCustomer | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(key(locationId));
    return raw ? (JSON.parse(raw) as RememberedCustomer) : null;
  } catch {
    return null;
  }
}

export function rememberCustomer(
  locationId: string,
  customer: RememberedCustomer,
  storage: Storage | undefined = safeStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(key(locationId), JSON.stringify(customer));
  } catch {
    // A full or blocked store is fine — the page works without the convenience.
  }
}

function safeStorage(): Storage | undefined {
  try {
    return typeof window !== "undefined" ? window.localStorage : undefined;
  } catch {
    return undefined;
  }
}
