// TypeScript mirrors of the backend schemas (src/void_pos/api/schemas.py).
// Only the shapes the POS uses are modelled here.

export interface MenuItem {
  id: string;
  location_id: string;
  name: string;
  kitchen_name: string | null;
  category: string | null;
  base_price_cents: number;
  tax_rate: number;
  is_available: boolean;
}

/** One order line. A modifier is a child line pointing at its item via parent_line_id. */
export interface LineIn {
  id: string;
  parent_line_id?: string | null;
  menu_item_id?: string | null;
  modifier_id?: string | null;
  quantity: number;
  name_snapshot: string;
  kitchen_name_snapshot?: string | null;
  unit_price_cents: number;
  tax_rate_snapshot?: number;
  category_snapshot?: string | null;
}

/** The ids a till mints for one change; a retry sends the same ones. */
export interface CommandIn {
  event_id: string;
  seq: number;
  occurred_at?: string | null;
}

export interface PlaceOrderRequest extends CommandIn {
  order_id: string;
  order_number: number;
  channel: "pos" | "voice" | "qr" | "web" | "aggregator";
  fulfillment: string;
  customer_id?: string | null;
  business_date?: string | null;
  lines: LineIn[];
}

export interface OrderResponse {
  id: string;
  order_number: number;
  state: string;
  business_date: string;
  total_cents: number;
  replayed: boolean;
}

export interface KitchenResponse {
  order_id: string;
  state: string;
  printed: boolean;
  ticket_bytes: number;
  replayed: boolean;
}

export interface PayRequest extends CommandIn {
  payment_id: string;
  method: "cash" | "external";
  external_ref?: string | null;
  amount_cents?: number | null;
  currency?: string;
}

export interface PaymentResponse {
  order_id: string;
  payment_id: string;
  provider: string;
  amount_cents: number;
  currency: string;
  status: string;
  order_state: string;
  replayed: boolean;
}

export interface DeviceOut {
  id: string;
  location_id: string;
  name: string;
  order_number_start: number;
  order_number_end: number;
  last_seq: number;
  paired: boolean;
  paired_at: string | null;
  revoked_at: string | null;
}

export interface LocationOut {
  id: string;
  org_id: string;
  name: string;
  timezone: string;
}

export interface DeviceSession {
  device: DeviceOut;
  location: LocationOut;
  next_order_number: number;
}

export interface PairResponse extends DeviceSession {
  token: string;
}

export interface Closeout {
  location_id: string;
  business_date: string;
  generated_at: string;
  orders: {
    total: number;
    open: number;
    sent: number;
    paid: number;
    voided: number;
  };
  gross_sales_cents: number;
  payments_by_provider: Record<string, number>;
  revenue_by_class_cents: Record<string, number>;
}
