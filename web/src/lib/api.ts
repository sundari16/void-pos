// Typed client for the Void POS API. Every till call sends its bearer token; every error
// comes back as an ApiError carrying the server's stable `code` and its fields.

import { apiBase } from "./config";
import type {
  AddLinesRequest,
  AdjustRequest,
  AvailabilityRequest,
  Closeout,
  CommandIn,
  DeviceSession,
  KitchenResponse,
  MenuItem,
  OrderResponse,
  OrderSummary,
  PairResponse,
  PayRequest,
  PaymentResponse,
  PlaceOrderRequest,
  PullResponse,
  ReceiptOut,
  RefundRequest,
  RemoveLinesRequest,
  StripeConfigOut,
  StripeIntentOut,
  SyncAck,
  SyncEvent,
  TillMenu,
  VoidRequest,
  VoidResponse,
} from "./types";

export class ApiError extends Error {
  readonly status: number; // 0 = the server couldn't be reached
  readonly code: string;
  readonly detail: string;
  readonly body: Record<string, unknown>;

  constructor(status: number, code: string, detail: string, body: Record<string, unknown> = {}) {
    super(`${status} ${code}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.detail = detail;
    this.body = body;
  }
}

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

interface RequestOptions {
  method?: "GET" | "POST";
  token?: string;
  body?: unknown;
  query?: Record<string, string | undefined>;
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

export function createApi(fetchImpl: Fetch = (input, init) => fetch(input, init), base = () => apiBase()) {
  async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(opts.query ?? {})) {
      if (value !== undefined) params.set(key, value);
    }
    const qs = params.toString() ? `?${params}` : "";
    const headers: Record<string, string> = {};
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    if (opts.token) headers.Authorization = `Bearer ${opts.token}`;

    let res: Response;
    try {
      res = await fetchImpl(`${base()}${path}${qs}`, {
        method: opts.method ?? "GET",
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch {
      // fetch rejects without a response on network or CORS failure.
      throw new ApiError(0, "network_error", `can't reach the server at ${base()}`);
    }
    if (!res.ok) throw await toApiError(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  return {
    pair(pairingCode: string): Promise<PairResponse> {
      return request("/devices/pair", { method: "POST", body: { pairing_code: pairingCode } });
    },
    me(token: string): Promise<DeviceSession> {
      return request("/devices/me", { token });
    },
    tillMenu(token: string, locationId: string): Promise<TillMenu> {
      return request(`/locations/${locationId}/menu`, { token });
    },
    placeOrder(token: string, body: PlaceOrderRequest): Promise<OrderResponse> {
      return request("/orders", { method: "POST", token, body });
    },
    /** The location's unsettled orders (open or sent), newest first. */
    openOrders(token: string, locationId: string): Promise<OrderSummary[]> {
      return request(`/locations/${locationId}/orders`, { token });
    },
    getOrder(token: string, orderId: string): Promise<OrderResponse> {
      return request(`/orders/${orderId}`, { token });
    },
    addLines(token: string, orderId: string, body: AddLinesRequest): Promise<OrderResponse> {
      return request(`/orders/${orderId}/lines`, { method: "POST", token, body });
    },
    removeLines(token: string, orderId: string, body: RemoveLinesRequest): Promise<OrderResponse> {
      return request(`/orders/${orderId}/remove-lines`, { method: "POST", token, body });
    },
    adjust(token: string, orderId: string, body: AdjustRequest): Promise<OrderResponse> {
      return request(`/orders/${orderId}/adjustments`, { method: "POST", token, body });
    },
    removeAdjustment(
      token: string,
      orderId: string,
      adjustmentId: string,
      body: CommandIn,
    ): Promise<OrderResponse> {
      return request(`/orders/${orderId}/adjustments/${adjustmentId}/remove`, {
        method: "POST",
        token,
        body,
      });
    },
    refund(token: string, orderId: string, body: RefundRequest): Promise<OrderResponse> {
      return request(`/orders/${orderId}/refunds`, { method: "POST", token, body });
    },
    stripeConfig(token: string): Promise<StripeConfigOut> {
      return request("/payments/stripe", { token });
    },
    stripeIntent(
      token: string,
      orderId: string,
      body: { payment_id: string; amount_cents: number; tip_cents: number },
    ): Promise<StripeIntentOut> {
      return request(`/orders/${orderId}/stripe-intent`, { method: "POST", token, body });
    },
    receipt(token: string, orderId: string): Promise<ReceiptOut> {
      return request(`/orders/${orderId}/receipt`, { token });
    },
    printReceipt(token: string, orderId: string): Promise<{ printed: boolean }> {
      return request(`/orders/${orderId}/print-receipt`, { method: "POST", token });
    },
    setAvailability(token: string, itemId: string, body: AvailabilityRequest): Promise<MenuItem> {
      return request(`/menu/items/${itemId}/availability`, { method: "POST", token, body });
    },
    sendToKitchen(token: string, orderId: string, body: CommandIn): Promise<KitchenResponse> {
      return request(`/orders/${orderId}/send-to-kitchen`, { method: "POST", token, body });
    },
    voidOrder(token: string, orderId: string, body: VoidRequest): Promise<VoidResponse> {
      return request(`/orders/${orderId}/void`, { method: "POST", token, body });
    },
    payOrder(token: string, orderId: string, body: PayRequest): Promise<PaymentResponse> {
      return request(`/orders/${orderId}/pay`, { method: "POST", token, body });
    },
    closeout(token: string, locationId: string, businessDate?: string): Promise<Closeout> {
      return request(`/reports/${locationId}/closeout`, {
        token,
        query: { business_date: businessDate },
      });
    },
    /** Send this till's queued events; idempotent by event id. One ack per event. */
    syncPush(token: string, events: SyncEvent[]): Promise<{ acks: SyncAck[] }> {
      return request("/sync/push", { method: "POST", token, body: { events } });
    },
    /** Other tills' events at this location after `cursor`, in commit order. */
    syncPull(token: string, cursor: string | null, limit = 500): Promise<PullResponse> {
      return request("/sync/pull", {
        token,
        query: { cursor: cursor ?? undefined, limit: String(limit) },
      });
    },
  };
}

export type Api = ReturnType<typeof createApi>;

export const api = createApi();
