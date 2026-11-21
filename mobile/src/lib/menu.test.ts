// Generated from web/src/lib/menu.test.ts by mobile/scripts/sync-lib.mjs — edit the web copy.
import {
  chosenModifiers,
  groupsFor,
  inWindow,
  itemOnSale,
  limitLabel,
  localClock,
  selectionProblems,
  setCount,
  toggle,
  windowLabel,
} from "./menu";
import type { ModifierGroup, TillMenu, TillMenuItem } from "./types";

function group(id: string, min: number, max: number | null, extra: Partial<ModifierGroup> = {}): ModifierGroup {
  return {
    id,
    location_id: "loc",
    name: id,
    min_select: min,
    max_select: max,
    available_from: null,
    available_to: null,
    available_now: true,
    modifiers: ["a", "b", "c"].map((m) => ({
      id: `${id}-${m}`,
      group_id: id,
      name: m.toUpperCase(),
      kitchen_name: null,
      price_delta_cents: 100,
    })),
    ...extra,
  };
}

const item: TillMenuItem = {
  id: "dosa",
  location_id: "loc",
  name: "Masala Dosa",
  kitchen_name: null,
  category: "Mains",
  base_price_cents: 1850,
  tax_rate: 0.1,
  is_available: true,
  available_from: null,
  available_to: null,
  modifier_group_ids: ["spice", "gone", "extras"],
  available_now: true,
};

describe("availability", () => {
  it("reads the location's wall clock", () => {
    // 13:30 UTC is 23:30 in Brisbane (UTC+10, no daylight saving).
    expect(localClock("Australia/Brisbane", new Date("2026-11-18T13:30:05Z"))).toBe("23:30:05");
    expect(localClock("Australia/Brisbane", new Date("2026-11-18T14:00:00Z"))).toBe("00:00:00");
  });

  it.each([
    [null, null, "03:00:00", true],
    ["11:00:00", "15:00:00", "11:00:00", true],
    ["11:00:00", "15:00:00", "15:00:00", false],
    ["22:00:00", "02:00:00", "23:30:00", true],
    ["22:00:00", "02:00:00", "01:59:59", true],
    ["22:00:00", "02:00:00", "12:00:00", false],
    ["17:00:00", null, "16:59:59", false],
    [null, "11:00:00", "10:59:59", true],
  ])("window %s–%s at %s is %s", (from, to, at, expected) => {
    expect(inWindow(from, to, at)).toBe(expected);
  });

  it("needs the item switched on as well as in its window", () => {
    expect(itemOnSale(item, "12:00:00")).toBe(true);
    expect(itemOnSale({ ...item, is_available: false }, "12:00:00")).toBe(false);
    expect(itemOnSale({ ...item, available_from: "17:00:00" }, "12:00:00")).toBe(false);
  });

  it("labels windows for staff", () => {
    expect(windowLabel("11:00:00", "15:00:00")).toBe("11:00–15:00");
    expect(windowLabel("17:00:00", null)).toBe("From 17:00");
    expect(windowLabel(null, "11:00:00")).toBe("Until 11:00");
    expect(windowLabel(null, null)).toBeNull();
  });
});

describe("modifier choices", () => {
  const spice = group("spice", 1, 1);
  const extras = group("extras", 0, 2);

  it("resolves an item's groups and skips ones no longer on the menu", () => {
    const menu: TillMenu = {
      location_id: "loc",
      timezone: "Australia/Brisbane",
      local_time: "12:00:00",
      items: [item],
      modifier_groups: [extras, spice],
    };
    expect(groupsFor(item, menu).map((g) => g.id)).toEqual(["spice", "extras"]);
  });

  it("swaps the choice in a pick-one group and toggles in others", () => {
    let sel = toggle(spice, {}, "spice-a");
    sel = toggle(spice, sel, "spice-b");
    expect(sel).toEqual({ "spice-b": 1 });
    sel = toggle(extras, sel, "extras-a");
    sel = toggle(extras, sel, "extras-b");
    expect(sel).toEqual({ "spice-b": 1, "extras-a": 1, "extras-b": 1 });
    expect(toggle(extras, sel, "extras-a")).toEqual({ "spice-b": 1, "extras-b": 1 });
  });

  it("keeps quantities within the group's maximum", () => {
    let sel = setCount(extras, {}, "extras-a", 5);
    expect(sel).toEqual({ "extras-a": 2 });
    sel = setCount(extras, sel, "extras-a", 1);
    sel = setCount(extras, sel, "extras-b", 3);
    expect(sel).toEqual({ "extras-a": 1, "extras-b": 1 });
    expect(setCount(extras, sel, "extras-b", 0)).toEqual({ "extras-a": 1 });
  });

  it("says what is missing before the item can be added", () => {
    expect(selectionProblems([spice, extras], {}, "12:00:00")).toEqual(["Choose one from spice"]);
    expect(selectionProblems([spice, extras], { "spice-a": 1 }, "12:00:00")).toEqual([]);
    expect(
      selectionProblems([group("sides", 2, null)], { "sides-a": 1 }, "12:00:00"),
    ).toEqual(["Choose at least 2 from sides"]);

    const lunchOnly = group("lunch", 1, 1, { available_from: "11:00:00", available_to: "15:00:00" });
    expect(selectionProblems([lunchOnly], {}, "18:00:00")).toEqual([]);
    expect(selectionProblems([lunchOnly], { "lunch-a": 1 }, "18:00:00")).toEqual([
      "lunch isn't available now",
    ]);
  });

  it("describes each group's limits", () => {
    expect(limitLabel(group("g", 0, null))).toBe("Optional");
    expect(limitLabel(group("g", 0, 3))).toBe("Up to 3");
    expect(limitLabel(group("g", 1, 1))).toBe("Choose 1");
    expect(limitLabel(group("g", 1, 2))).toBe("Choose 1–2");
    expect(limitLabel(group("g", 2, null))).toBe("At least 2");
  });

  it("lists the chosen modifiers in menu order with their quantities", () => {
    const chosen = chosenModifiers([spice, extras], { "extras-b": 2, "spice-c": 1 });
    expect(chosen.map((c) => [c.modifier.id, c.quantity])).toEqual([
      ["spice-c", 1],
      ["extras-b", 2],
    ]);
  });
});
