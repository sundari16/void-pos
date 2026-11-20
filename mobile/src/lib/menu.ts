// The till's menu: what is on sale now, and which modifier choices an item allows.
// Mirrors the backend's rules (src/void_pos/menu/availability.py, orders/rules.py), so the
// till greys out what the server would refuse. Times are local "HH:MM:SS" strings in the
// location's timezone, which compare correctly as plain strings.

import type { Modifier, ModifierGroup, TillMenu, TillMenuItem } from "./types";

/** The wall-clock time in `timeZone` as "HH:MM:SS". */
export function localClock(timeZone: string, now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "00";
  return `${part("hour")}:${part("minute")}:${part("second")}`;
}

/** [from, to) in local time; a window ending before it starts runs past midnight. */
export function inWindow(from: string | null, to: string | null, at: string): boolean {
  if (from === null) return to === null || at < to;
  if (to === null) return at >= from;
  if (from < to) return from <= at && at < to;
  return at >= from || at < to;
}

export function itemOnSale(item: TillMenuItem, at: string): boolean {
  return item.is_available && inWindow(item.available_from, item.available_to, at);
}

export function groupOnSale(group: ModifierGroup, at: string): boolean {
  return inWindow(group.available_from, group.available_to, at);
}

/** "From 11:00" / "Until 15:00" / "11:00–15:00" for an item's window, or null. */
export function windowLabel(from: string | null, to: string | null): string | null {
  const hm = (t: string) => t.slice(0, 5);
  if (from && to) return `${hm(from)}–${hm(to)}`;
  if (from) return `From ${hm(from)}`;
  if (to) return `Until ${hm(to)}`;
  return null;
}

export function groupsFor(item: TillMenuItem, menu: TillMenu): ModifierGroup[] {
  const byId = new Map(menu.modifier_groups.map((g) => [g.id, g]));
  return item.modifier_group_ids.flatMap((id) => byId.get(id) ?? []);
}

/** Chosen modifiers, by modifier id, with how many of each (per unit of the item). */
export type Selection = Record<string, number>;

export function chosenCount(group: ModifierGroup, selection: Selection): number {
  return group.modifiers.reduce((sum, m) => sum + (selection[m.id] ?? 0), 0);
}

/** Tap a modifier: a pick-one group swaps its choice; otherwise it toggles on or off. */
export function toggle(group: ModifierGroup, selection: Selection, modifierId: string): Selection {
  const next = { ...selection };
  if (next[modifierId]) {
    delete next[modifierId];
    return next;
  }
  if (group.max_select === 1) for (const m of group.modifiers) delete next[m.id];
  next[modifierId] = 1;
  return next;
}

/** Change how many of a chosen modifier, within the group's maximum. 0 removes it. */
export function setCount(
  group: ModifierGroup,
  selection: Selection,
  modifierId: string,
  count: number,
): Selection {
  const next = { ...selection };
  const others = chosenCount(group, selection) - (selection[modifierId] ?? 0);
  const limit = group.max_select === null ? 99 : group.max_select - others;
  const clamped = Math.max(0, Math.min(count, limit));
  if (clamped === 0) delete next[modifierId];
  else next[modifierId] = clamped;
  return next;
}

/** What still has to change before the item can go on the order, as plain sentences. */
export function selectionProblems(
  groups: ModifierGroup[],
  selection: Selection,
  at: string,
): string[] {
  const problems: string[] = [];
  for (const group of groups) {
    const count = chosenCount(group, selection);
    if (!groupOnSale(group, at)) {
      if (count > 0) problems.push(`${group.name} isn't available now`);
      continue;
    }
    if (count < group.min_select) {
      problems.push(`Choose ${group.min_select === 1 ? "one" : `at least ${group.min_select}`} from ${group.name}`);
    } else if (group.max_select !== null && count > group.max_select) {
      problems.push(`Choose at most ${group.max_select} from ${group.name}`);
    }
  }
  return problems;
}

/** "Choose 1", "Up to 3", "Choose 1–2", "Optional" for a group's heading. */
export function limitLabel(group: ModifierGroup): string {
  const { min_select: min, max_select: max } = group;
  if (min === 0) return max === null ? "Optional" : `Up to ${max}`;
  if (max === min) return `Choose ${min}`;
  return max === null ? `At least ${min}` : `Choose ${min}–${max}`;
}

export interface ChosenModifier {
  modifier: Modifier;
  quantity: number;
}

export function chosenModifiers(groups: ModifierGroup[], selection: Selection): ChosenModifier[] {
  return groups.flatMap((g) =>
    g.modifiers.flatMap((m) => (selection[m.id] ? [{ modifier: m, quantity: selection[m.id] }] : [])),
  );
}
