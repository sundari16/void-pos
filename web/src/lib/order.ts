// The draft order the till is building, before it is placed.
// Money stays in integer cents throughout; the item->modifier tree is expressed with
// client-local `ref` / `parentRef` ids that map straight onto the backend's LineIn.

import type { LineIn, MenuItem } from "./types";
import type { QuickModifier } from "./modifiers";

export interface DraftModifier {
  ref: string;
  name: string;
  kitchenName?: string;
  unitPriceCents: number; // the price delta
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

let counter = 0;
/** A short unique ref for the lifetime of the page. */
export function newRef(prefix: string): string {
  counter += 1;
  return `${prefix}-${counter}-${Math.random().toString(36).slice(2, 7)}`;
}

export function draftItemFrom(
  item: MenuItem,
  quantity: number,
  chosen: QuickModifier[],
): DraftItem {
  return {
    ref: newRef("item"),
    menuItemId: item.id,
    name: item.name,
    kitchenName: item.kitchen_name ?? undefined,
    category: item.category ?? undefined,
    unitPriceCents: item.base_price_cents,
    taxRate: item.tax_rate,
    quantity,
    modifiers: chosen.map((m) => ({
      ref: newRef("mod"),
      name: m.name,
      kitchenName: m.kitchenName,
      unitPriceCents: m.priceDeltaCents,
    })),
  };
}

/** Subtotal for one item line: (item + its modifiers, each as a child line) x qty rules.
 *  Mirrors the backend's order_total_cents: sum(unit_price_cents * quantity) over every
 *  line. Item lines carry the item quantity; modifier child lines carry quantity 1. */
export function itemSubtotalCents(item: DraftItem): number {
  const modsEach = item.modifiers.reduce((sum, m) => sum + m.unitPriceCents, 0);
  return item.unitPriceCents * item.quantity + modsEach;
}

export function orderTotalCents(items: DraftItem[]): number {
  return items.reduce((sum, it) => sum + itemSubtotalCents(it), 0);
}

export function orderLineCount(items: DraftItem[]): number {
  return items.reduce((sum, it) => sum + it.quantity, 0);
}

/** Flatten the draft into the backend's LineIn list (items then their modifier children). */
export function toLineIns(items: DraftItem[]): LineIn[] {
  const lines: LineIn[] = [];
  for (const item of items) {
    lines.push({
      ref: item.ref,
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
        ref: mod.ref,
        parent_ref: item.ref,
        quantity: 1,
        name_snapshot: mod.name,
        kitchen_name_snapshot: mod.kitchenName ?? null,
        unit_price_cents: mod.unitPriceCents,
      });
    }
  }
  return lines;
}
