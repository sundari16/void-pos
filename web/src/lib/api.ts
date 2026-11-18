// Typed client for the Void POS API. Every till call sends its bearer token; every error
// comes back as an ApiError carrying the server's stable `code` and its fields.

import { apiBase } from "./config";
import type {
  Closeout,
  CommandIn,
  DeviceSession,
  KitchenResponse,
  MenuItem,
  OrderResponse,
  PairResponse,
  PayRequest,
  PaymentResponse,
  PlaceOrderRequest,
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
    listMenuItems(token: string, locationId: string): Promise<MenuItem[]> {
      return request(`/locations/${locationId}/menu/items`, { token });
    },
    placeOrder(token: string, body: PlaceOrderRequest): Promise<OrderResponse> {
      return request("/orders", { method: "POST", token, body });
    },
    sendToKitchen(token: string, orderId: string, body: CommandIn): Promise<KitchenResponse> {
      return request(`/orders/${orderId}/send-to-kitchen`, { method: "POST", token, body });
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
  };
}

export type Api = ReturnType<typeof createApi>;

export const api = createApi();
