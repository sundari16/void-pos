import { describe, expect, it } from "vitest";
import { formatCents, formatDelta } from "./money";
import { draftItemFrom, orderTotalCents, toLineIns } from "./order";
import type { MenuItem } from "./types";
import { ULID_PATTERN } from "./ulid";

const dosa: MenuItem = {
  id: "01JITEM0000000000000000000",
  location_id: "01JLOCATION000000000000000",
  name: "Masala Dosa",
  kitchen_name: "மசாலா தோசை",
  category: "Mains",
  base_price_cents: 1850,
  tax_rate: 0.1,
  is_available: true,
  available_from: null,
  available_to: null,
};

const extraCheese = {
  modifier: {
    id: "01JMODIFIER000000000000000",
    group_id: "01JGROUP000000000000000000",
    name: "Extra cheese",
    kitchen_name: null,
    price_delta_cents: 150,
  },
  quantity: 1,
};

describe("draft orders", () => {
  it("become item lines followed by modifier child lines with ULID ids", () => {
    const item = draftItemFrom(dosa, 2, [extraCheese]);
    const [itemLine, modLine] = toLineIns([item]);

    expect(itemLine.id).toMatch(ULID_PATTERN);
    expect(itemLine).toMatchObject({
      menu_item_id: dosa.id,
      quantity: 2,
      unit_price_cents: 1850,
      kitchen_name_snapshot: "மசாலா தோசை",
      category_snapshot: "Mains",
    });
    expect(modLine).toMatchObject({
      parent_line_id: itemLine.id,
      modifier_id: extraCheese.modifier.id,
      quantity: 1,
      unit_price_cents: 150,
      tax_rate_snapshot: 0.1,
      category_snapshot: "Mains",
    });
    expect(modLine.id).not.toBe(itemLine.id);
    expect(itemLine.id < modLine.id).toBe(true); // sorts item first
  });

  it("total like the server: modifiers are charged per unit of their item", () => {
    expect(orderTotalCents([draftItemFrom(dosa, 2, [extraCheese])])).toBe(2 * (1850 + 150));

    const item = draftItemFrom(dosa, 3, [{ ...extraCheese, quantity: 2 }]);
    expect(orderTotalCents([item])).toBe(3 * (1850 + 2 * 150));
    expect(toLineIns([item])[1].quantity).toBe(2);
  });

  it("send a kitchen note as a free-text modifier line", () => {
    const item = draftItemFrom(dosa, 1, [], "  nut allergy  ");
    const [, note] = toLineIns([item]);
    expect(note).toMatchObject({
      parent_line_id: item.ref,
      modifier_id: null,
      name_snapshot: "nut allergy",
      unit_price_cents: 0,
    });
    expect(toLineIns([draftItemFrom(dosa, 1, [], "   ")])).toHaveLength(1);
  });
});

describe("money", () => {
  it("formats cents for display only", () => {
    expect(formatCents(1850)).toBe("$18.50");
    expect(formatDelta(150)).toBe("+$1.50");
    expect(formatDelta(-50)).toBe("-$0.50");
    expect(formatDelta(0)).toBe("");
  });
});
