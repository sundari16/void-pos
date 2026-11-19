import {
  chosenModifiers,
  inWindow,
  itemOnSale,
  selectionProblems,
  setCount,
  toggle,
  type Selection,
} from "./menu";
import type { ModifierGroup, TillMenuItem } from "./types";

function group(min: number, max: number | null): ModifierGroup {
  return {
    id: "g",
    location_id: "loc",
    name: "Spice",
    min_select: min,
    max_select: max,
    available_from: null,
    available_to: null,
    available_now: true,
    modifiers: ["mild", "medium", "hot"].map((m) => ({
      id: m,
      group_id: "g",
      name: m,
      kitchen_name: null,
      price_delta_cents: 0,
    })),
  };
}

function item(overrides: Partial<TillMenuItem> = {}): TillMenuItem {
  return {
    id: "i",
    location_id: "loc",
    name: "Thali",
    kitchen_name: null,
    category: null,
    base_price_cents: 1800,
    tax_rate: 0,
    is_available: true,
    available_from: null,
    available_to: null,
    modifier_group_ids: ["g"],
    available_now: true,
    ...overrides,
  };
}

describe("inWindow", () => {
  it("handles open-ended and overnight windows", () => {
    expect(inWindow("11:00:00", "15:00:00", "12:00:00")).toBe(true);
    expect(inWindow("11:00:00", "15:00:00", "15:00:00")).toBe(false);
    expect(inWindow(null, null, "03:00:00")).toBe(true);
    // Overnight: 22:00 -> 02:00
    expect(inWindow("22:00:00", "02:00:00", "23:30:00")).toBe(true);
    expect(inWindow("22:00:00", "02:00:00", "12:00:00")).toBe(false);
  });
});

describe("itemOnSale", () => {
  it("is off when unavailable or outside its window", () => {
    expect(itemOnSale(item(), "12:00:00")).toBe(true);
    expect(itemOnSale(item({ is_available: false }), "12:00:00")).toBe(false);
    expect(itemOnSale(item({ available_from: "17:00:00", available_to: null }), "12:00:00")).toBe(false);
  });
});

describe("toggle / setCount", () => {
  it("a pick-one group swaps its choice", () => {
    const g = group(1, 1);
    let sel: Selection = {};
    sel = toggle(g, sel, "mild");
    sel = toggle(g, sel, "hot");
    expect(sel).toEqual({ hot: 1 });
  });

  it("setCount clamps to the group maximum", () => {
    const g = group(0, 2);
    let sel: Selection = {};
    sel = setCount(g, sel, "mild", 5);
    expect(sel.mild).toBe(2);
  });
});

describe("selectionProblems", () => {
  it("asks for a required choice until it is made", () => {
    const g = group(1, 1);
    expect(selectionProblems([g], {}, "12:00:00").length).toBe(1);
    expect(selectionProblems([g], { mild: 1 }, "12:00:00")).toEqual([]);
  });
});

describe("chosenModifiers", () => {
  it("lists the picked modifiers with their counts", () => {
    const g = group(0, 3);
    const chosen = chosenModifiers([g], { hot: 2 });
    expect(chosen).toHaveLength(1);
    expect(chosen[0].modifier.id).toBe("hot");
    expect(chosen[0].quantity).toBe(2);
  });
});
