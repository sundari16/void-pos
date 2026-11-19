// Pure helpers for the back-office menu and sales screens: grouping items by category,
// describing a menu change for people, and laying out an order's lines.

import { formatCents, formatDelta } from "./money";
import type { MenuChange, OrderLineOut, TillMenu } from "./adminTypes";
import { displayTime } from "./adminForms";

export const UNCATEGORISED = "Uncategorised";

export interface CategorySection<T> {
  category: string;
  items: T[];
}

/** Items under their category, categories A to Z, uncategorised last, items by name. */
export function groupByCategory<T extends { name: string; category: string | null }>(
  items: T[],
): CategorySection<T>[] {
  const sections = new Map<string, T[]>();
  for (const item of items) {
    const key = item.category?.trim() || UNCATEGORISED;
    const list = sections.get(key) ?? [];
    list.push(item);
    sections.set(key, list);
  }
  return [...sections.entries()]
    .sort(([a], [b]) => {
      if (a === UNCATEGORISED) return 1;
      if (b === UNCATEGORISED) return -1;
      return a.localeCompare(b);
    })
    .map(([category, list]) => ({
      category,
      items: [...list].sort((x, y) => x.name.localeCompare(y.name)),
    }));
}

/** The distinct categories in use, A to Z, for suggesting one in the item form. */
export function categoriesOf(items: { category: string | null }[]): string[] {
  const names = new Set<string>();
  for (const item of items) if (item.category?.trim()) names.add(item.category.trim());
  return [...names].sort((a, b) => a.localeCompare(b));
}

// --- change history ------------------------------------------------------------------

/** Names for ids that appear in changes: items, groups and modifiers on the menu now. */
export function menuNames(menu: TillMenu | null): Map<string, string> {
  const names = new Map<string, string>();
  if (!menu) return names;
  for (const item of menu.items) names.set(item.id, item.name);
  for (const group of menu.modifier_groups) {
    names.set(group.id, group.name);
    for (const m of group.modifiers) names.set(m.id, m.name);
  }
  return names;
}

const ENTITY: Record<string, string> = {
  menu_item: "item",
  modifier_group: "modifier group",
  modifier: "modifier",
};

const FIELD: Record<string, string> = {
  name: "name",
  kitchen_name: "kitchen name",
  category: "category",
  base_price_cents: "price",
  price_delta_cents: "price",
  tax_rate: "GST",
  is_available: "availability",
  available_from: "from",
  available_to: "until",
  min_select: "min",
  max_select: "max",
};

function fieldValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") {
    if (field === "max_select") return "no limit";
    return "none";
  }
  if (field === "base_price_cents" && typeof value === "number") return formatCents(value);
  if (field === "price_delta_cents" && typeof value === "number") {
    return formatDelta(value) || formatCents(0);
  }
  if (field === "tax_rate" && typeof value === "number") return `${Math.round(value * 1000) / 10}%`;
  if (field === "is_available") return value ? "on sale" : "sold out";
  if (field === "available_from" || field === "available_to") return displayTime(String(value));
  return String(value);
}

function nameIn(snapshot: Record<string, unknown> | null): string | null {
  const name = snapshot?.name;
  return typeof name === "string" ? name : null;
}

/** One line for people: what changed, on what. */
export function describeChange(change: MenuChange, names: Map<string, string>): string {
  const kind = ENTITY[change.entity] ?? change.entity;
  const subject =
    names.get(change.entity_id) ?? nameIn(change.after) ?? nameIn(change.before) ?? `a ${kind}`;
  const prefix = change.reverts_id ? "Undo: " : "";
  switch (change.action) {
    case "create":
      return `${prefix}Added ${kind} “${subject}”`;
    case "delete":
      return `${prefix}Deleted ${kind} “${subject}”`;
    case "attach":
    case "detach": {
      const ref = (change.after ?? change.before)?.modifier_group_id;
      const group = typeof ref === "string" ? (names.get(ref) ?? "a modifier group") : "a group";
      const verb = change.action === "attach" ? "added" : "removed";
      return `${prefix}${subject}: ${verb} group “${group}”`;
    }
    case "update": {
      const before = change.before ?? {};
      const after = change.after ?? {};
      const fields = Object.keys(after);
      if (fields.length === 1 && fields[0] === "is_available") {
        return `${prefix}${subject}: ${after.is_available ? "back on sale" : "sold out"}`;
      }
      const parts = fields.map(
        (f) => `${FIELD[f] ?? f} ${fieldValue(f, before[f])} → ${fieldValue(f, after[f])}`,
      );
      return `${prefix}${subject}: ${parts.join(", ") || "updated"}`;
    }
    default:
      return `${prefix}${subject}: ${change.action}`;
  }
}

/** Change id -> the number of the change that undid it. */
export function undoneBy(changes: MenuChange[]): Map<string, number> {
  const undone = new Map<string, number>();
  for (const c of changes) if (c.reverts_id) undone.set(c.reverts_id, c.number);
  return undone;
}

// --- order lines ---------------------------------------------------------------------

export interface LineRow {
  id: string;
  name: string;
  quantity: number;
  amountCents: number;
  isModifier: boolean;
}

/** An order's lines as rows, each item followed by its modifiers. A modifier's quantity is
 *  per unit of its item, so its amount is price x quantity x the item's quantity. */
export function lineRows(allLines: OrderLineOut[]): LineRow[] {
  const lines = allLines.filter((line) => !line.removed); // taken off the order
  const children = new Map<string, OrderLineOut[]>();
  for (const line of lines) {
    if (line.parent_line_id) {
      const list = children.get(line.parent_line_id) ?? [];
      list.push(line);
      children.set(line.parent_line_id, list);
    }
  }
  const rows: LineRow[] = [];
  for (const line of lines) {
    if (line.parent_line_id) continue;
    rows.push({
      id: line.id,
      name: line.name,
      quantity: line.quantity,
      amountCents: line.unit_price_cents * line.quantity,
      isModifier: false,
    });
    for (const child of children.get(line.id) ?? []) {
      rows.push({
        id: child.id,
        name: child.name,
        quantity: child.quantity,
        amountCents: child.unit_price_cents * child.quantity * line.quantity,
        isModifier: true,
      });
    }
  }
  return rows;
}
