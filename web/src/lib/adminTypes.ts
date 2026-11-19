// TypeScript mirrors of the admin-side schemas (src/void_pos/api/schemas.py). Shapes the
// till already models (menu, orders, receipts, closeout, devices) come from types.ts.

import type { DeviceOut, LocationOut, Modifier, ModifierGroup } from "./types";

export type {
  Closeout,
  DeviceOut,
  MenuItem,
  Modifier,
  ModifierGroup,
  OrderLineOut,
  OrderResponse,
  OrderSummary,
  ReceiptOut,
  TillMenu,
  TillMenuItem,
} from "./types";

/** A location with the details its customer receipts show. */
export interface AdminLocation extends LocationOut {
  business_name: string | null;
  abn: string | null;
  address: string | null;
  phone: string | null;
  receipt_footer: string | null;
}

/** PATCH /admin/locations/{id}: a blank value clears a field. */
export interface VenueDetailsIn {
  business_name: string;
  abn: string;
  address: string;
  phone: string;
  receipt_footer: string;
  /** The public-holiday surcharge the tills offer, in basis points (1500 = 15%); 0 clears. */
  holiday_surcharge_bp?: number;
}

/** POST /locations/{id}/menu/items. Times are local, "HH:MM". */
export interface MenuItemIn {
  name: string;
  base_price_cents: number;
  kitchen_name: string | null;
  category: string | null;
  tax_rate: number;
  is_available: boolean;
  available_from: string | null;
  available_to: string | null;
}

/** PATCH /menu/items/{id}: only the fields to change. */
export type MenuItemPatch = Partial<MenuItemIn>;

export interface ModifierGroupIn {
  name: string;
  min_select: number;
  max_select: number | null;
  available_from: string | null;
  available_to: string | null;
}

export type ModifierGroupPatch = Partial<ModifierGroupIn>;

/** A modifier group as the edit endpoints return it (no modifiers, no availability). */
export type ModifierGroupOut = Omit<ModifierGroup, "modifiers" | "available_now">;

export interface ModifierIn {
  name: string;
  price_delta_cents: number;
  kitchen_name: string | null;
}

export type ModifierPatch = Partial<ModifierIn>;

export type ModifierOut = Modifier;

/** One numbered entry in a location's menu history (GET /locations/{id}/menu/changes). */
export interface MenuChange {
  id: string;
  number: number;
  entity: "menu_item" | "modifier_group" | "modifier" | string;
  entity_id: string;
  action: "create" | "update" | "delete" | "attach" | "detach" | string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  reverts_id: string | null;
  actor: string;
  changed_at: string;
}

export type StaffRole = "staff" | "manager";

export interface StaffOut {
  id: string;
  location_id: string;
  name: string;
  role: StaffRole | string;
  active: boolean;
}

export interface StaffIn {
  name: string;
  role: StaffRole;
  pin: string;
}

export interface DeviceIn {
  name: string;
  order_number_start: number;
  order_number_end: number;
}

export interface PairingCodeOut {
  pairing_code: string;
  expires_at: string;
}

export interface DeviceCreated extends PairingCodeOut {
  device: DeviceOut;
}

export type OrderState = "open" | "sent" | "paid" | "voided";
