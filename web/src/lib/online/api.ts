// Typed client for the public storefront API (/online/*). No token: these are the calls a
// customer's browser makes. Errors come back as the shared ApiError with the server's code.

import { ApiError } from "../api";
import { apiBase } from "../config";
import type {
  CustomerOrders,
  LoyaltyBalance,
  PlaceWebOrderRequest,
  Storefront,
  WebOrder,
  WebPayment,
  WebStripeIntent,
} from "./types";

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

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

export function createOnlineApi(
  fetchImpl: Fetch = (input, init) => fetch(input, init),
  base = () => apiBase(),
) {
  async function request<T>(
    path: string,
    opts: { method?: "GET" | "POST"; body?: unknown } = {},
  ): Promise<T> {
    const headers: Record<string, string> = {};
    if (opts.body !== undefined) headers["Content-Type"] = "application/json";
    let res: Response;
    try {
      res = await fetchImpl(`${base()}/online${path}`, {
        method: opts.method ?? "GET",
        headers,
        body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      });
    } catch {
      throw new ApiError(0, "network_error", `can't reach the server at ${base()}`);
    }
    if (!res.ok) throw await toApiError(res);
    return (await res.json()) as T;
  }

  return {
    storefront(locationId: string): Promise<Storefront> {
      return request(`/locations/${locationId}`);
    },
    placeOrder(locationId: string, body: PlaceWebOrderRequest): Promise<WebOrder> {
      return request(`/locations/${locationId}/orders`, { method: "POST", body });
    },
    getOrder(orderId: string): Promise<WebOrder["order"]> {
      return request(`/orders/${orderId}`);
    },
    stripeIntent(
      orderId: string,
      body: { payment_id: string; tip_cents: number },
    ): Promise<WebStripeIntent> {
      return request(`/orders/${orderId}/stripe-intent`, { method: "POST", body });
    },
    payCard(
      orderId: string,
      body: { payment_id: string; stripe_payment_intent_id: string; tip_cents: number },
    ): Promise<WebPayment> {
      return request(`/orders/${orderId}/pay`, { method: "POST", body });
    },
    customerOrders(customerId: string): Promise<CustomerOrders> {
      return request(`/customers/${customerId}/orders`);
    },
    customerLoyalty(customerId: string): Promise<LoyaltyBalance> {
      return request(`/customers/${customerId}/loyalty`);
    },
  };
}

export type OnlineApi = ReturnType<typeof createOnlineApi>;

export const onlineApi = createOnlineApi();
