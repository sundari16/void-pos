// TypeScript mirrors of the storefront schemas (src/void_pos/api/online_schemas.py).
// The customer-facing API is public (no token) and reuses the shared order/menu shapes.

import type { LineIn, OrderResponse, OrderSummary, TillMenu } from "../types";

export interface LoyaltyInfo {
  enabled: boolean;
  earn_points_per_dollar: number;
  redeem_cents_per_point: number;
}

/** GET /online/locations/{id}: the menu plus what the storefront can do here. */
export interface Storefront {
  location_id: string;
  location_name: string;
  timezone: string;
  currency: string;
  online_pay_enabled: boolean;
  loyalty: LoyaltyInfo;
  menu: TillMenu;
}

export interface OnlineCustomer {
  name: string;
  phone?: string | null;
  email?: string | null;
  marketing_opt_in: boolean;
}

export interface PlaceWebOrderRequest {
  order_id: string;
  customer: OnlineCustomer;
  fulfillment: "pickup" | "delivery";
  delivery_address?: string | null;
  customer_note?: string | null;
  lines: LineIn[];
  redeem_points: number;
}

/** The order as the customer sees it: the shared order shape plus the online-only fields. */
export interface OnlineOrder extends OrderResponse {
  customer_id: string | null;
  delivery_address: string | null;
  customer_note: string | null;
}

export interface WebOrder {
  order: OnlineOrder;
  customer_id: string;
  loyalty_balance: number;
  points_earned: number;
  redeemed_points: number;
  redeemed_cents: number;
}

export interface WebStripeIntent {
  payment_intent_id: string;
  client_secret: string;
  publishable_key: string;
}

export interface WebPayment {
  order_id: string;
  payment_id: string;
  provider: string;
  amount_cents: number;
  tip_cents: number;
  currency: string;
  status: string;
  order_state: string;
  balance_cents: number;
  replayed: boolean;
}

export interface LoyaltyBalance {
  customer_id: string;
  enabled: boolean;
  balance: number;
  earn_points_per_dollar: number;
  redeem_cents_per_point: number;
}

export interface CustomerOrders {
  customer_id: string;
  orders: OrderSummary[];
}
