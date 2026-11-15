// Typed client for the Void POS FastAPI backend.
// Base URL comes from NEXT_PUBLIC_API_BASE (see config.ts). All calls run in the
// browser, so the backend must allow this origin via CORS.

import { config } from "./config";
import type {
  Closeout,
  KitchenResponse,
  MenuItem,
  OrderResponse,
  PayRequest,
  PaymentResponse,
  PlaceOrderRequest,
} from "./types";

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${config.apiBase}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (err) {
    // Network / CORS failure — fetch rejects without a response.
    throw new ApiError(0, `cannot reach backend at ${config.apiBase} (${String(err)})`);
  }

  if (!res.ok) {
    throw new ApiError(res.status, await extractDetail(res));
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

async function extractDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail.map((d: { msg?: string }) => d.msg ?? JSON.stringify(d)).join("; ");
    }
    return JSON.stringify(body);
  } catch {
    return res.statusText || "request failed";
  }
}

export const api = {
  listMenuItems(locationId: string): Promise<MenuItem[]> {
    return request<MenuItem[]>(`/locations/${locationId}/menu/items`);
  },

  placeOrder(body: PlaceOrderRequest): Promise<OrderResponse> {
    return request<OrderResponse>("/orders", { method: "POST", body: JSON.stringify(body) });
  },

  sendToKitchen(orderId: string): Promise<KitchenResponse> {
    return request<KitchenResponse>(`/orders/${orderId}/send-to-kitchen`, { method: "POST" });
  },

  payOrder(orderId: string, body: PayRequest): Promise<PaymentResponse> {
    return request<PaymentResponse>(`/orders/${orderId}/pay`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  closeout(locationId: string, businessDate?: string): Promise<Closeout> {
    const qs = businessDate ? `?business_date=${businessDate}` : "";
    return request<Closeout>(`/reports/${locationId}/closeout${qs}`);
  },
};

export { config };
