import type { ChosenModifier } from "./menu";
import { draftItemFrom, itemSubtotalCents, orderTotalCents, toLineIns } from "./order";
import type { MenuItem, Modifier } from "./types";

function menuItem(overrides: Partial<MenuItem> = {}): MenuItem {
  return {
    id: "item1",
    location_id: "loc",
    name: "Naan",
    kitchen_name: null,
    category: "Breads",
    base_price_cents: 350,
    tax_rate: 0.1,
    is_available: true,
    available_from: null,
    available_to: null,
    ...overrides,
  };
}

function modifier(id: string, delta: number): Modifier {
  return { id, group_id: "g", name: id, kitchen_name: null, price_delta_cents: delta };
}

describe("draft order", () => {
  const chosen: ChosenModifier[] = [{ modifier: modifier("butter", 150), quantity: 1 }];

  it("prices an item with its modifiers, per unit of the item", () => {
    const draft = draftItemFrom(menuItem(), 2, chosen);
    // (base 350 + butter 150) * 2
    expect(itemSubtotalCents(draft)).toBe(1000);
    expect(orderTotalCents([draft])).toBe(1000);
  });

  it("adds a kitchen note as a free, price-0 modifier line", () => {
    const draft = draftItemFrom(menuItem(), 1, chosen, "extra crispy");
    expect(draft.modifiers).toHaveLength(2);
    const note = draft.modifiers[1];
    expect(note.name).toBe("extra crispy");
    expect(note.unitPriceCents).toBe(0);
    expect(note.modifierId).toBeUndefined();
  });

  it("flattens to backend lines: item first, then its modifiers pointing at it", () => {
    const draft = draftItemFrom(menuItem(), 2, chosen);
    const lines = toLineIns([draft]);
    expect(lines).toHaveLength(2);
    expect(lines[0].menu_item_id).toBe("item1");
    expect(lines[0].parent_line_id).toBeUndefined();
    expect(lines[0].quantity).toBe(2);
    expect(lines[1].parent_line_id).toBe(lines[0].id);
    expect(lines[1].modifier_id).toBe("butter");
    expect(lines[1].unit_price_cents).toBe(150);
    // A modifier is taxed like its item.
    expect(lines[1].tax_rate_snapshot).toBe(0.1);
  });
});
