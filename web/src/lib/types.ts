// TypeScript mirrors of the backend schemas (src/void_pos/api/schemas.py).
// Only the shapes the POS flow uses are modelled here.

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

/** One requested line. `ref` is a client-local id unique within the request;
 *  a modifier points at its parent item line via `parent_ref`. */
export interface LineIn {
  ref: string;
  parent_ref?: string | null;
  menu_item_id?: string | null;
  modifier_id?: string | null;
  quantity: number;
  name_snapshot: string;
  kitchen_name_snapshot?: string | null;
  unit_price_cents: number;
  tax_rate_snapshot?: number;
  category_snapshot?: string | null;
}

export interface PlaceOrderRequest {
  device_id: string;
  location_id: string;
  order_number: number;
  channel: string;
  fulfillment: string;
  customer_id?: string | null;
  business_date?: string | null;
  lines: LineIn[];
}

export interface OrderResponse {
  id: string;
  order_number: number;
  state: string;
}

export interface KitchenResponse {
  order_id: string;
  state: string;
  ticket_bytes: number;
}

export interface PayRequest {
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
  status: string;
  order_state: string;
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
