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
  /** Daily window in local time, "HH:MM:SS"; past midnight when to < from. */
  available_from: string | null;
  available_to: string | null;
}

export interface TillMenuItem extends MenuItem {
  modifier_group_ids: string[];
  available_now: boolean;
}

export interface Modifier {
  id: string;
  group_id: string;
  name: string;
  kitchen_name: string | null;
  price_delta_cents: number;
}

export interface ModifierGroup {
  id: string;
  location_id: string;
  name: string;
  min_select: number;
  max_select: number | null;
  available_from: string | null;
  available_to: string | null;
  modifiers: Modifier[];
  available_now: boolean;
}

/** GET /locations/{id}/menu: everything the till needs to ring up an order. */
export interface TillMenu {
  location_id: string;
  timezone: string;
  local_time: string;
  items: TillMenuItem[];
  modifier_groups: ModifierGroup[];
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
  /** Dine-in: set on item lines only; a modifier follows its item. */
  seat_number?: number | null;
  course_number?: number | null;
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
  table_label?: string | null;
  lines: LineIn[];
}

/** More items for a placed order. */
export interface AddLinesRequest extends CommandIn {
  lines: LineIn[];
}

/** A line of a placed order, as the server has it. */
export interface OrderLineOut {
  id: string;
  parent_line_id: string | null;
  menu_item_id: string | null;
  modifier_id: string | null;
  quantity: number;
  name: string;
  kitchen_name: string | null;
  unit_price_cents: number;
  tax_rate: number;
  category: string | null;
  seat_number: number | null;
  course_number: number | null;
  /** The kitchen has it. */
  sent: boolean;
}

export interface PaymentOut {
  payment_id: string;
  provider: string;
  external_ref: string | null;
  amount_cents: number;
  currency: string;
  status: string;
}

/** An order in a list (GET /locations/{id}/orders). */
export interface OrderSummary {
  id: string;
  order_number: number;
  state: string;
  fulfillment: string;
  table_label: string | null;
  business_date: string;
  created_at: string;
  total_cents: number;
  gst_cents: number;
  balance_cents: number;
  unsent_lines: number;
}

/** The whole order (place, add items, read). */
export interface OrderResponse extends OrderSummary {
  lines: OrderLineOut[];
  payments: PaymentOut[];
  replayed: boolean;
}

export interface KitchenResponse {
  order_id: string;
  state: string;
  printed: boolean;
  ticket_bytes: number;
  /** Lines the kitchen hadn't had; 0 means the ticket was a reprint. */
  lines_sent: number;
  replayed: boolean;
}

export interface VoidRequest extends CommandIn {
  reason: string;
  /** Needed once the venue has a manager. */
  manager_pin?: string | null;
}

export interface AvailabilityRequest {
  is_available: boolean;
  manager_pin?: string | null;
}

export interface ReceiptOut {
  title: string;
  venue_name: string;
  address: string | null;
  phone: string | null;
  abn: string | null;
  order_number: number;
  table_label: string | null;
  ordered_at: string;
  printed_at: string;
  lines: { quantity: number; name: string; amount_cents: number; is_modifier: boolean }[];
  total_cents: number;
  gst_cents: number;
  payments: { label: string; amount_cents: number; reference: string | null }[];
  balance_cents: number;
  voided: boolean;
  footer: string | null;
}

export interface VoidResponse {
  order_id: string;
  state: string;
  printed: boolean;
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
  /** Still owing after this payment; the order is paid at 0. */
  balance_cents: number;
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
  paid_sales_cents: number;
  gst_cents: number;
  sales_ex_gst_cents: number;
  open_balance_cents: number;
  payments_by_provider: Record<string, number>;
  revenue_by_class_cents: Record<string, number>;
}
