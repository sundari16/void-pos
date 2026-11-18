// Typed client for the back office. Every call sends the venue admin token; every error
// comes back as the till client's ApiError, carrying the server's `code`, `detail` and fields.

import { ApiError } from "./api";
import { apiBase } from "./config";
import type {
  AdminLocation,
  Closeout,
  DeviceCreated,
  DeviceIn,
  DeviceOut,
  MenuChange,
  MenuItem,
  MenuItemIn,
  MenuItemPatch,
  ModifierGroupIn,
  ModifierGroupOut,
  ModifierGroupPatch,
  ModifierIn,
  ModifierOut,
  ModifierPatch,
  OrderResponse,
  OrderState,
  OrderSummary,
  PairingCodeOut,
  ReceiptOut,
  StaffIn,
  StaffOut,
  TillMenu,
  VenueDetailsIn,
} from "./adminTypes";

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  /** A list value repeats the parameter: state=open&state=sent. */
  query?: Record<string, string | string[] | undefined>;
}

async function toApiError(res: Response): Promise<ApiError> {
  try {
    const body = (await res.json()) as Record<string, unknown>;
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body);
    const code = typeof body.code === "string" ? body.code : `http_${res.status}`;
    return new ApiError(res.status, code, detail, body);
  } catch {
    return new ApiError(res.status, `http_${res.status}`, res.statusText || "request failed");
  }
}

const enc = encodeURIComponent;

export function createAdminApi(
  token: string,
  fetchImpl: Fetch = (input, init) => fetch(input, init),
  base = () => apiBase(),
) {
  async function send(path: string, opts: RequestOptions): Promise<Response> {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(opts.query ?? {})) {
      if (value === undefined) continue;
      for (const v of Array.isArray(value) ? value : [value]) params.append(key, v);
    }
    const qs = params.toString() ? `?${params}` : "";
    const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";

    let res: Response;
    try {
      res = await fetchImpl(`${base()}${path}${qs}`, {
        method: opts.method ?? "GET",
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch {
      throw new ApiError(0, "network_error", `can't reach the server at ${base()}`);
    }
    if (!res.ok) throw await toApiError(res);
    return res;
  }

  async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await send(path, opts);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  return {
    // --- venue -------------------------------------------------------------------
    locations(): Promise<AdminLocation[]> {
      return request("/admin/locations");
    },
    updateVenue(locationId: string, body: VenueDetailsIn): Promise<AdminLocation> {
      return request(`/admin/locations/${enc(locationId)}`, { method: "PATCH", body });
    },

    // --- menu --------------------------------------------------------------------
    menu(locationId: string): Promise<TillMenu> {
      return request(`/locations/${enc(locationId)}/menu`);
    },
    createItem(locationId: string, body: MenuItemIn): Promise<MenuItem> {
      return request(`/locations/${enc(locationId)}/menu/items`, { method: "POST", body });
    },
    updateItem(itemId: string, body: MenuItemPatch): Promise<MenuItem> {
      return request(`/menu/items/${enc(itemId)}`, { method: "PATCH", body });
    },
    deleteItem(itemId: string): Promise<void> {
      return request(`/menu/items/${enc(itemId)}`, { method: "DELETE" });
    },
    /** Sold out (false) or back on (true); the admin token needs no manager PIN. */
    setAvailable(itemId: string, isAvailable: boolean): Promise<MenuItem> {
      return request(`/menu/items/${enc(itemId)}/availability`, {
        method: "POST",
        body: { is_available: isAvailable },
      });
    },
    createGroup(locationId: string, body: ModifierGroupIn): Promise<ModifierGroupOut> {
      return request(`/locations/${enc(locationId)}/menu/modifier-groups`, {
        method: "POST",
        body,
      });
    },
    updateGroup(groupId: string, body: ModifierGroupPatch): Promise<ModifierGroupOut> {
      return request(`/menu/modifier-groups/${enc(groupId)}`, { method: "PATCH", body });
    },
    createModifier(groupId: string, body: ModifierIn): Promise<ModifierOut> {
      return request(`/menu/modifier-groups/${enc(groupId)}/modifiers`, {
        method: "POST",
        body,
      });
    },
    updateModifier(modifierId: string, body: ModifierPatch): Promise<ModifierOut> {
      return request(`/menu/modifiers/${enc(modifierId)}`, { method: "PATCH", body });
    },
    attachGroup(itemId: string, groupId: string): Promise<void> {
      return request(`/menu/items/${enc(itemId)}/modifier-groups/${enc(groupId)}`, {
        method: "POST",
      });
    },
    menuChanges(locationId: string, limit = 50): Promise<MenuChange[]> {
      return request(`/locations/${enc(locationId)}/menu/changes`, {
        query: { limit: String(limit) },
      });
    },
    revertChange(changeId: string): Promise<MenuChange> {
      return request(`/menu/changes/${enc(changeId)}/revert`, { method: "POST" });
    },

    // --- staff -------------------------------------------------------------------
    staff(locationId: string, includeInactive = false): Promise<StaffOut[]> {
      return request(`/admin/locations/${enc(locationId)}/staff`, {
        query: { include_inactive: includeInactive ? "true" : undefined },
      });
    },
    addStaff(locationId: string, body: StaffIn): Promise<StaffOut> {
      return request(`/admin/locations/${enc(locationId)}/staff`, { method: "POST", body });
    },
    setPin(staffId: string, pin: string): Promise<StaffOut> {
      return request(`/admin/staff/${enc(staffId)}/pin`, { method: "POST", body: { pin } });
    },
    deactivateStaff(staffId: string): Promise<StaffOut> {
      return request(`/admin/staff/${enc(staffId)}/deactivate`, { method: "POST" });
    },

    // --- tills -------------------------------------------------------------------
    devices(locationId: string): Promise<DeviceOut[]> {
      return request(`/admin/locations/${enc(locationId)}/devices`);
    },
    addDevice(locationId: string, body: DeviceIn): Promise<DeviceCreated> {
      return request(`/admin/locations/${enc(locationId)}/devices`, { method: "POST", body });
    },
    pairingCode(deviceId: string): Promise<PairingCodeOut> {
      return request(`/admin/devices/${enc(deviceId)}/pairing-code`, { method: "POST" });
    },
    revokeDevice(deviceId: string): Promise<DeviceOut> {
      return request(`/admin/devices/${enc(deviceId)}/revoke`, { method: "POST" });
    },

    // --- sales -------------------------------------------------------------------
    closeout(locationId: string, businessDate: string): Promise<Closeout> {
      return request(`/reports/${enc(locationId)}/closeout`, {
        query: { business_date: businessDate },
      });
    },
    orders(locationId: string, businessDate: string, states: OrderState[]): Promise<OrderSummary[]> {
      return request(`/locations/${enc(locationId)}/orders`, {
        query: { business_date: businessDate, state: states, limit: "500" },
      });
    },
    order(orderId: string): Promise<OrderResponse> {
      return request(`/orders/${enc(orderId)}`);
    },
    receipt(orderId: string): Promise<ReceiptOut> {
      return request(`/orders/${enc(orderId)}/receipt`);
    },
    printReceipt(orderId: string): Promise<{ printed: boolean }> {
      return request(`/orders/${enc(orderId)}/print-receipt`, { method: "POST" });
    },
    /** The location's full export (JSON), as a file to save. */
    async exportBlob(locationId: string): Promise<Blob> {
      const res = await send(`/export/${enc(locationId)}`, {});
      return res.blob();
    },
  };
}

export type AdminApi = ReturnType<typeof createAdminApi>;

/** The text to show for a failed call: the server's own `detail` when it answered. */
export function errorDetail(err: unknown): string {
  if (err instanceof ApiError) {
    return err.status === 0 ? "Can't reach the Void POS server. Check the connection and try again." : err.detail;
  }
  return err instanceof Error ? err.message : String(err);
}
