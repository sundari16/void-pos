// Pure helpers for the back-office forms: dollars typed by people <-> integer cents, local
// times, and checking each form before it goes to the server. Every validator returns
// either the request body or the errors to show next to each field.

import type {
  DeviceIn,
  DeviceOut,
  MenuItem,
  MenuItemIn,
  MenuItemPatch,
  ModifierGroupIn,
  ModifierGroupOut,
  ModifierGroupPatch,
  ModifierIn,
  ModifierOut,
  ModifierPatch,
  StaffIn,
  StaffRole,
} from "./adminTypes";

/** The server's per-line typo guard ($100,000). */
export const MAX_LINE_CENTS = 10_000_000;

export type Errors<F> = Partial<Record<keyof F, string>>;
export type Checked<T, F> = { value: T } | { errors: Errors<F> };

// --- money -----------------------------------------------------------------------

/** "12.50", "$1,250", "-0.50" (when allowed) -> cents, or an error to show. */
export function dollarsToCents(
  input: string,
  { allowNegative = false }: { allowNegative?: boolean } = {},
): { cents: number } | { error: string } {
  let text = input.trim().replace(/,/g, "");
  let sign = 1;
  if (text.startsWith("-")) {
    if (!allowNegative) return { error: "Enter an amount of 0 or more." };
    sign = -1;
    text = text.slice(1).trim();
  }
  text = text.replace(/^\$/, "");
  if (!/^(\d+(\.\d{0,2})?|\.\d{1,2})$/.test(text)) {
    return { error: "Enter an amount like 12.50." };
  }
  const [whole, frac = ""] = text.split(".");
  const cents = Number(whole || "0") * 100 + Number(frac.padEnd(2, "0"));
  if (cents > MAX_LINE_CENTS) return { error: "That amount is too large." };
  return { cents: sign * cents || 0 };
}

/** Cents as people type them: 1250 -> "12.50", -50 -> "-0.50". */
export function centsToDollars(cents: number): string {
  const abs = Math.abs(Math.trunc(cents));
  const text = `${Math.floor(abs / 100)}.${String(abs % 100).padStart(2, "0")}`;
  return cents < 0 ? `-${text}` : text;
}

// --- local times -------------------------------------------------------------------

/** A typed local time as "HH:MM", or null when left blank. */
export function parseTime(input: string): { value: string | null } | { error: string } {
  const text = input.trim();
  if (!text) return { value: null };
  const m = /^(\d{1,2}):(\d{2})(?::\d{2})?$/.exec(text);
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) {
    return { error: "Use a 24-hour time like 11:30." };
  }
  return { value: `${m[1].padStart(2, "0")}:${m[2]}` };
}

/** The server's "HH:MM:SS" as "HH:MM"; null as "". */
export function displayTime(value: string | null | undefined): string {
  return value ? value.slice(0, 5) : "";
}

/** A daily window for people: "11:00–15:00", "from 17:00", "until 10:30", or "". */
export function windowLabel(from: string | null, to: string | null): string {
  const f = displayTime(from);
  const t = displayTime(to);
  if (f && t) return `${f}–${t}`;
  if (f) return `from ${f}`;
  if (t) return `until ${t}`;
  return "";
}

function checkWindow<F extends { available_from: string; available_to: string }>(
  form: F,
  errors: Errors<F>,
): { from: string | null; to: string | null } {
  const from = parseTime(form.available_from);
  const to = parseTime(form.available_to);
  if ("error" in from) errors.available_from = from.error;
  if ("error" in to) errors.available_to = to.error;
  const fromValue = "value" in from ? from.value : null;
  const toValue = "value" in to ? to.value : null;
  if (fromValue && toValue && fromValue === toValue) {
    errors.available_to = "The window can't start and end at the same time.";
  }
  return { from: fromValue, to: toValue };
}

function blankToNull(text: string): string | null {
  const t = text.trim();
  return t ? t : null;
}

function changed<T extends object>(before: Partial<T>, after: T): Partial<T> {
  const patch: Partial<T> = {};
  for (const key of Object.keys(after) as (keyof T)[]) {
    if (before[key] !== after[key]) patch[key] = after[key];
  }
  return patch;
}

// --- menu items --------------------------------------------------------------------

export const TAX_RATES = [
  { value: "0.1", label: "10% GST" },
  { value: "0", label: "GST free (0%)" },
] as const;

export interface ItemForm {
  name: string;
  price: string;
  category: string;
  kitchen_name: string;
  tax_rate: string;
  is_available: boolean;
  available_from: string;
  available_to: string;
}

export function itemForm(item?: MenuItem): ItemForm {
  if (!item) {
    return {
      name: "",
      price: "",
      category: "",
      kitchen_name: "",
      tax_rate: "0.1",
      is_available: true,
      available_from: "",
      available_to: "",
    };
  }
  return {
    name: item.name,
    price: centsToDollars(item.base_price_cents),
    category: item.category ?? "",
    kitchen_name: item.kitchen_name ?? "",
    tax_rate: String(item.tax_rate),
    is_available: item.is_available,
    available_from: displayTime(item.available_from),
    available_to: displayTime(item.available_to),
  };
}

export function checkItem(form: ItemForm): Checked<MenuItemIn, ItemForm> {
  const errors: Errors<ItemForm> = {};
  const name = form.name.trim();
  if (!name) errors.name = "Give the item a name.";
  else if (name.length > 200) errors.name = "Keep the name under 200 characters.";
  const price = dollarsToCents(form.price);
  if ("error" in price) errors.price = price.error;
  const tax = Number(form.tax_rate);
  if (!form.tax_rate.trim() || !Number.isFinite(tax) || tax < 0 || tax > 1) {
    errors.tax_rate = "Pick a tax rate.";
  }
  if (form.category.trim().length > 120) errors.category = "Keep the category under 120 characters.";
  if (form.kitchen_name.trim().length > 200) {
    errors.kitchen_name = "Keep the kitchen name under 200 characters.";
  }
  const hours = checkWindow(form, errors);
  if (Object.keys(errors).length > 0 || "error" in price) return { errors };
  return {
    value: {
      name,
      base_price_cents: price.cents,
      category: blankToNull(form.category),
      kitchen_name: blankToNull(form.kitchen_name),
      tax_rate: tax,
      is_available: form.is_available,
      available_from: hours.from,
      available_to: hours.to,
    },
  };
}

/** Only the fields that differ from the item as the server has it. */
export function itemPatch(item: MenuItem, next: MenuItemIn): MenuItemPatch {
  return changed<MenuItemIn>(
    {
      name: item.name,
      base_price_cents: item.base_price_cents,
      category: item.category,
      kitchen_name: item.kitchen_name,
      tax_rate: item.tax_rate,
      is_available: item.is_available,
      available_from: displayTime(item.available_from) || null,
      available_to: displayTime(item.available_to) || null,
    },
    next,
  );
}

// --- modifier groups and modifiers -------------------------------------------------

export interface GroupForm {
  name: string;
  min_select: string;
  max_select: string;
  available_from: string;
  available_to: string;
}

export function groupForm(group?: ModifierGroupOut): GroupForm {
  return {
    name: group?.name ?? "",
    min_select: String(group?.min_select ?? 0),
    max_select: group?.max_select == null ? "" : String(group.max_select),
    available_from: displayTime(group?.available_from),
    available_to: displayTime(group?.available_to),
  };
}

function count(text: string): number | null {
  const t = text.trim();
  if (!/^\d+$/.test(t)) return null;
  const n = Number(t);
  return n <= 100 ? n : null;
}

export function checkGroup(form: GroupForm): Checked<ModifierGroupIn, GroupForm> {
  const errors: Errors<GroupForm> = {};
  const name = form.name.trim();
  if (!name) errors.name = "Give the group a name.";
  const min = count(form.min_select || "0");
  if (min === null) errors.min_select = "A whole number from 0 to 100.";
  const max = form.max_select.trim() ? count(form.max_select) : null;
  if (form.max_select.trim() && max === null) errors.max_select = "A whole number up to 100, or blank.";
  else if (max !== null && min !== null && max < min) {
    errors.max_select = "Can't be less than the minimum.";
  }
  const hours = checkWindow(form, errors);
  if (Object.keys(errors).length > 0) return { errors };
  return {
    value: {
      name,
      min_select: min ?? 0,
      max_select: max,
      available_from: hours.from,
      available_to: hours.to,
    },
  };
}

export function groupPatch(group: ModifierGroupOut, next: ModifierGroupIn): ModifierGroupPatch {
  return changed<ModifierGroupIn>(
    {
      name: group.name,
      min_select: group.min_select,
      max_select: group.max_select,
      available_from: displayTime(group.available_from) || null,
      available_to: displayTime(group.available_to) || null,
    },
    next,
  );
}

/** "Pick 1", "Pick up to 3", "Pick 1 to 2", "Optional". */
export function selectionLabel(min: number, max: number | null): string {
  if (max === null) return min > 0 ? `Pick at least ${min}` : "Optional, any number";
  if (min === max) return `Pick ${min}`;
  if (min === 0) return `Optional, up to ${max}`;
  return `Pick ${min} to ${max}`;
}

export interface ModifierForm {
  name: string;
  price: string;
  kitchen_name: string;
}

export function modifierForm(modifier?: ModifierOut): ModifierForm {
  return {
    name: modifier?.name ?? "",
    price: modifier ? centsToDollars(modifier.price_delta_cents) : "0.00",
    kitchen_name: modifier?.kitchen_name ?? "",
  };
}

export function checkModifier(form: ModifierForm): Checked<ModifierIn, ModifierForm> {
  const errors: Errors<ModifierForm> = {};
  const name = form.name.trim();
  if (!name) errors.name = "Give the modifier a name.";
  const price = dollarsToCents(form.price || "0", { allowNegative: true });
  if ("error" in price) errors.price = price.error;
  if (Object.keys(errors).length > 0 || "error" in price) return { errors };
  return {
    value: { name, price_delta_cents: price.cents, kitchen_name: blankToNull(form.kitchen_name) },
  };
}

export function modifierPatch(modifier: ModifierOut, next: ModifierIn): ModifierPatch {
  return changed<ModifierIn>(
    {
      name: modifier.name,
      price_delta_cents: modifier.price_delta_cents,
      kitchen_name: modifier.kitchen_name,
    },
    next,
  );
}

// --- staff -------------------------------------------------------------------------

/** An error to show, or null when the PIN is 4 to 8 digits. */
export function pinError(pin: string): string | null {
  return /^[0-9]{4,8}$/.test(pin) ? null : "A PIN is 4 to 8 digits.";
}

export interface StaffForm {
  name: string;
  role: StaffRole;
  pin: string;
}

export function checkStaff(form: StaffForm): Checked<StaffIn, StaffForm> {
  const errors: Errors<StaffForm> = {};
  const name = form.name.trim();
  if (!name) errors.name = "Enter their name.";
  else if (name.length > 120) errors.name = "Keep the name under 120 characters.";
  if (form.role !== "staff" && form.role !== "manager") errors.role = "Pick a role.";
  const pin = pinError(form.pin);
  if (pin) errors.pin = pin;
  if (Object.keys(errors).length > 0) return { errors };
  return { value: { name, role: form.role, pin: form.pin } };
}

// --- tills -------------------------------------------------------------------------

export interface DeviceForm {
  name: string;
  start: string;
  end: string;
}

const MAX_ORDER_NUMBER = 2_147_483_647;

/** The next free block of order numbers after every existing till's, `size` long. */
export function suggestRange(devices: DeviceOut[], size = 1000): { start: number; end: number } {
  const highest = devices.reduce((max, d) => Math.max(max, d.order_number_end), 0);
  const start = highest === 0 ? 1000 : highest + 1;
  return { start, end: Math.min(start + size - 1, MAX_ORDER_NUMBER) };
}

export function checkDevice(form: DeviceForm): Checked<DeviceIn, DeviceForm> {
  const errors: Errors<DeviceForm> = {};
  const name = form.name.trim();
  if (!name) errors.name = "Name the till, e.g. Till 2.";
  const whole = (text: string) => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : NaN);
  const start = whole(form.start);
  const end = whole(form.end);
  if (!(start >= 1 && start <= MAX_ORDER_NUMBER)) errors.start = "A whole number, 1 or more.";
  if (!(end >= 1 && end <= MAX_ORDER_NUMBER)) errors.end = "A whole number, 1 or more.";
  else if (start >= 1 && end < start) errors.end = "Must be at or after the first number.";
  if (Object.keys(errors).length > 0) return { errors };
  return { value: { name, order_number_start: start, order_number_end: end } };
}
