// The draft order the till is building, before it is placed.
// Money stays in integer cents throughout. Each line's `ref` is a ULID minted when it is
// added, and becomes that line's id on the server; modifiers point at their item's ref.

import type { ChosenModifier } from "./menu";
import type { LineIn, MenuItem } from "./types";
import { ulid } from "./ulid";

export interface DraftModifier {
  ref: string;
  modifierId?: string; // absent for a free-text note to the kitchen
  name: string;
  kitchenName?: string;
  unitPriceCents: number; // the price delta
  quantity: number; // per unit of the item
}

export interface DraftItem {
  ref: string;
  menuItemId: string;
  name: string;
  kitchenName?: string;
  category?: string;
  unitPriceCents: number; // snapshot of base price at add time
  taxRate: number;
  quantity: number;
  modifiers: DraftModifier[];
}

export function draftItemFrom(
  item: MenuItem,
  quantity: number,
  chosen: ChosenModifier[],
  note?: string,
): DraftItem {
  // The item's id first: ids are time-ordered, so lines sort item before its modifiers.
  const ref = ulid();
  const modifiers: DraftModifier[] = chosen.map(({ modifier, quantity: count }) => ({
    ref: ulid(),
    modifierId: modifier.id,
    name: modifier.name,
    kitchenName: modifier.kitchen_name ?? undefined,
    unitPriceCents: modifier.price_delta_cents,
    quantity: count,
  }));
  const text = note?.trim();
  if (text) modifiers.push({ ref: ulid(), name: text, unitPriceCents: 0, quantity: 1 });
  return {
    ref,
    menuItemId: item.id,
    name: item.name,
    kitchenName: item.kitchen_name ?? undefined,
    category: item.category ?? undefined,
    unitPriceCents: item.base_price_cents,
    taxRate: item.tax_rate,
    quantity,
    modifiers,
  };
}

/** Subtotal for one item and its modifiers. A modifier's quantity is per unit of the
 *  item, so 2 naan with 1 extra butter charges the butter twice. Mirrors the backend's
 *  totals.total_cents. */
export function itemSubtotalCents(item: DraftItem): number {
  const modsEach = item.modifiers.reduce((sum, m) => sum + m.unitPriceCents * m.quantity, 0);
  return (item.unitPriceCents + modsEach) * item.quantity;
}

export function orderTotalCents(items: DraftItem[]): number {
  return items.reduce((sum, it) => sum + itemSubtotalCents(it), 0);
}

export function orderLineCount(items: DraftItem[]): number {
  return items.reduce((sum, it) => sum + it.quantity, 0);
}

/** Flatten the draft into the backend's lines (items, each followed by its modifiers). */
export function toLineIns(items: DraftItem[]): LineIn[] {
  const lines: LineIn[] = [];
  for (const item of items) {
    lines.push({
      id: item.ref,
      menu_item_id: item.menuItemId,
      quantity: item.quantity,
      name_snapshot: item.name,
      kitchen_name_snapshot: item.kitchenName ?? null,
      unit_price_cents: item.unitPriceCents,
      tax_rate_snapshot: item.taxRate,
      category_snapshot: item.category ?? null,
    });
    for (const mod of item.modifiers) {
      lines.push({
        id: mod.ref,
        parent_line_id: item.ref,
        modifier_id: mod.modifierId ?? null,
        quantity: mod.quantity,
        name_snapshot: mod.name,
        kitchen_name_snapshot: mod.kitchenName ?? null,
        unit_price_cents: mod.unitPriceCents,
        // A modifier is taxed like its item, and its revenue belongs to the item's class
        // (the basis for tip-outs).
        tax_rate_snapshot: item.taxRate,
        category_snapshot: item.category ?? null,
      });
    }
  }
  return lines;
}
