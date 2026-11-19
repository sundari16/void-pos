import { describe, expect, it } from "vitest";
import {
  categoriesOf,
  describeChange,
  groupByCategory,
  lineRows,
  undoneBy,
  UNCATEGORISED,
} from "./adminMenu";
import type { MenuChange, OrderLineOut } from "./adminTypes";

const item = (name: string, category: string | null) => ({ name, category });

describe("groupByCategory", () => {
  it("groups items A to Z with uncategorised last", () => {
    const sections = groupByCategory([
      item("Naan", "Sides"),
      item("Water", null),
      item("Dosa", "Mains"),
      item("Biryani", "Mains"),
      item("Gift card", "  "),
    ]);
    expect(sections.map((s) => s.category)).toEqual(["Mains", "Sides", UNCATEGORISED]);
    expect(sections[0].items.map((i) => i.name)).toEqual(["Biryani", "Dosa"]);
    expect(sections[2].items.map((i) => i.name)).toEqual(["Gift card", "Water"]);
  });

  it("lists the categories in use", () => {
    expect(categoriesOf([item("a", "Sides"), item("b", null), item("c", "Mains"), item("d", "Sides")])).toEqual([
      "Mains",
      "Sides",
    ]);
  });
});

function change(over: Partial<MenuChange>): MenuChange {
  return {
    id: "C1",
    number: 1,
    entity: "menu_item",
    entity_id: "ITEM1",
    action: "update",
    before: null,
    after: null,
    reverts_id: null,
    actor: "admin",
    changed_at: "2026-11-19T01:00:00Z",
    ...over,
  };
}

describe("describeChange", () => {
  const names = new Map([
    ["ITEM1", "Butter Chicken"],
    ["G1", "Spice level"],
  ]);

  it("describes a price change in dollars", () => {
    const text = describeChange(
      change({ before: { base_price_cents: 2200 }, after: { base_price_cents: 2350 } }),
      names,
    );
    expect(text).toBe("Butter Chicken: price $22.00 → $23.50");
  });

  it("describes several fields and cleared values", () => {
    const text = describeChange(
      change({
        before: { tax_rate: 0.1, category: "Mains" },
        after: { tax_rate: 0, category: null },
      }),
      names,
    );
    expect(text).toBe("Butter Chicken: GST 10% → 0%, category Mains → none");
  });

  it("calls a sold-out switch what it is", () => {
    expect(
      describeChange(change({ before: { is_available: true }, after: { is_available: false } }), names),
    ).toBe("Butter Chicken: sold out");
    expect(
      describeChange(change({ before: { is_available: false }, after: { is_available: true } }), names),
    ).toBe("Butter Chicken: back on sale");
  });

  it("names a deleted item from its snapshot", () => {
    const text = describeChange(
      change({ entity_id: "GONE", action: "delete", before: { name: "Thali" } }),
      names,
    );
    expect(text).toBe("Deleted item “Thali”");
  });

  it("describes attaching a group and an undo", () => {
    expect(
      describeChange(change({ action: "attach", after: { modifier_group_id: "G1" } }), names),
    ).toBe("Butter Chicken: added group “Spice level”");
    expect(
      describeChange(
        change({ action: "detach", before: { modifier_group_id: "G1" }, reverts_id: "C0" }),
        names,
      ),
    ).toBe("Undo: Butter Chicken: removed group “Spice level”");
  });

  it("shows a modifier's price as a delta", () => {
    const text = describeChange(
      change({
        entity: "modifier",
        entity_id: "M1",
        before: { price_delta_cents: 100, kitchen_name: null },
        after: { price_delta_cents: 0, kitchen_name: "BTR" },
      }),
      new Map([["M1", "Extra butter"]]),
    );
    expect(text).toBe("Extra butter: price +$1.00 → $0.00, kitchen name none → BTR");
  });

  it("knows which changes were undone, and by which", () => {
    const undone = undoneBy([
      change({ id: "C3", number: 3, reverts_id: "C2" }),
      change({ id: "C2", number: 2 }),
    ]);
    expect(undone.get("C2")).toBe(3);
    expect(undone.has("C3")).toBe(false);
  });
});

function line(over: Partial<OrderLineOut>): OrderLineOut {
  return {
    id: "L",
    parent_line_id: null,
    menu_item_id: null,
    modifier_id: null,
    quantity: 1,
    name: "",
    kitchen_name: null,
    unit_price_cents: 0,
    tax_rate: 0.1,
    category: null,
    seat_number: null,
    course_number: null,
    sent: true,
    removed: false,
    ...over,
  };
}

describe("lineRows", () => {
  it("puts each item's modifiers under it, charged per unit of the item", () => {
    const rows = lineRows([
      line({ id: "A", name: "Garlic Naan", quantity: 2, unit_price_cents: 450 }),
      line({ id: "B", name: "Lassi", quantity: 1, unit_price_cents: 650 }),
      line({ id: "A1", parent_line_id: "A", name: "Extra butter", quantity: 1, unit_price_cents: 100 }),
    ]);
    expect(rows.map((r) => [r.name, r.amountCents, r.isModifier])).toEqual([
      ["Garlic Naan", 900, false],
      ["Extra butter", 200, true],
      ["Lassi", 650, false],
    ]);
  });
});
