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
};

const extraCheese = { key: "cheese", name: "Extra cheese", priceDeltaCents: 150 };

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
      quantity: 1,
      unit_price_cents: 150,
      category_snapshot: "Mains",
    });
    expect(modLine.id).not.toBe(itemLine.id);
  });

  it("total like the server: each line's price times its quantity", () => {
    expect(orderTotalCents([draftItemFrom(dosa, 2, [extraCheese])])).toBe(1850 * 2 + 150);
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
