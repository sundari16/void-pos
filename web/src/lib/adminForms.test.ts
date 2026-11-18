import { describe, expect, it } from "vitest";
import {
  centsToDollars,
  checkDevice,
  checkGroup,
  checkItem,
  checkModifier,
  checkStaff,
  dollarsToCents,
  groupPatch,
  itemForm,
  itemPatch,
  modifierPatch,
  parseTime,
  pinError,
  selectionLabel,
  suggestRange,
  windowLabel,
  type ItemForm,
} from "./adminForms";
import type { DeviceOut, MenuItem } from "./adminTypes";

const dosa: MenuItem = {
  id: "ITEM1",
  location_id: "LOC",
  name: "Masala Dosa",
  kitchen_name: null,
  category: "Mains",
  base_price_cents: 1850,
  tax_rate: 0.1,
  is_available: true,
  available_from: "11:00:00",
  available_to: "15:00:00",
};

describe("dollars and cents", () => {
  it("reads what people type", () => {
    expect(dollarsToCents("12.50")).toEqual({ cents: 1250 });
    expect(dollarsToCents("$18")).toEqual({ cents: 1800 });
    expect(dollarsToCents(" 1,250.5 ")).toEqual({ cents: 125050 });
    expect(dollarsToCents(".5")).toEqual({ cents: 50 });
    expect(dollarsToCents("0")).toEqual({ cents: 0 });
  });

  it("never goes through floats", () => {
    expect(dollarsToCents("0.29")).toEqual({ cents: 29 });
    expect(dollarsToCents("1.15")).toEqual({ cents: 115 });
  });

  it("refuses what isn't an amount", () => {
    for (const bad of ["", "abc", "12.345", "1.2.3", "$", "12,50 AUD"]) {
      expect(dollarsToCents(bad)).toHaveProperty("error");
    }
  });

  it("takes a negative only when allowed", () => {
    expect(dollarsToCents("-0.50")).toHaveProperty("error");
    expect(dollarsToCents("-0.50", { allowNegative: true })).toEqual({ cents: -50 });
    expect(dollarsToCents("-$2", { allowNegative: true })).toEqual({ cents: -200 });
    expect(dollarsToCents("-0", { allowNegative: true })).toEqual({ cents: 0 });
  });

  it("guards against a typo the size of a house", () => {
    expect(dollarsToCents("100000")).toEqual({ cents: 10_000_000 });
    expect(dollarsToCents("100000.01")).toHaveProperty("error");
  });

  it("writes cents back as people type them", () => {
    expect(centsToDollars(1250)).toBe("12.50");
    expect(centsToDollars(5)).toBe("0.05");
    expect(centsToDollars(-50)).toBe("-0.50");
    expect(centsToDollars(0)).toBe("0.00");
  });
});

describe("local times", () => {
  it("normalises a typed time", () => {
    expect(parseTime("9:30")).toEqual({ value: "09:30" });
    expect(parseTime("11:30:00")).toEqual({ value: "11:30" });
    expect(parseTime("  ")).toEqual({ value: null });
    expect(parseTime("24:00")).toHaveProperty("error");
    expect(parseTime("11.30")).toHaveProperty("error");
  });

  it("labels a daily window", () => {
    expect(windowLabel("11:00:00", "15:00:00")).toBe("11:00–15:00");
    expect(windowLabel("17:00:00", null)).toBe("from 17:00");
    expect(windowLabel(null, "10:30:00")).toBe("until 10:30");
    expect(windowLabel(null, null)).toBe("");
  });
});

describe("item form", () => {
  const form = (over: Partial<ItemForm> = {}): ItemForm => ({ ...itemForm(dosa), ...over });

  it("starts from the item as the server has it", () => {
    expect(itemForm(dosa)).toEqual({
      name: "Masala Dosa",
      price: "18.50",
      category: "Mains",
      kitchen_name: "",
      tax_rate: "0.1",
      is_available: true,
      available_from: "11:00",
      available_to: "15:00",
    });
    expect(itemForm().tax_rate).toBe("0.1");
  });

  it("sends cents, null for blanks and HH:MM times", () => {
    const checked = checkItem(form({ price: "19", kitchen_name: "  ", available_to: "" }));
    expect(checked).toEqual({
      value: {
        name: "Masala Dosa",
        base_price_cents: 1900,
        category: "Mains",
        kitchen_name: null,
        tax_rate: 0.1,
        is_available: true,
        available_from: "11:00",
        available_to: null,
      },
    });
  });

  it("names each field that's wrong", () => {
    const checked = checkItem(form({ name: " ", price: "x", available_from: "25:00" }));
    expect(checked).toEqual({
      errors: {
        name: expect.any(String),
        price: expect.any(String),
        available_from: expect.any(String),
      },
    });
  });

  it("refuses a window that starts and ends together, like the server", () => {
    const checked = checkItem(form({ available_from: "11:00", available_to: "11:00" }));
    expect(checked).toHaveProperty("errors.available_to");
  });

  it("patches only what changed", () => {
    const checked = checkItem(form({ price: "20.00" }));
    if (!("value" in checked)) throw new Error("expected a value");
    expect(itemPatch(dosa, checked.value)).toEqual({ base_price_cents: 2000 });

    const cleared = checkItem(form({ category: "", available_from: "", available_to: "" }));
    if (!("value" in cleared)) throw new Error("expected a value");
    expect(itemPatch(dosa, cleared.value)).toEqual({
      category: null,
      available_from: null,
      available_to: null,
    });
  });

  it("patches nothing when nothing changed", () => {
    const checked = checkItem(form());
    if (!("value" in checked)) throw new Error("expected a value");
    expect(itemPatch(dosa, checked.value)).toEqual({});
  });
});

describe("modifier groups and modifiers", () => {
  const group = {
    id: "G",
    location_id: "LOC",
    name: "Spice level",
    min_select: 1,
    max_select: 1,
    available_from: null,
    available_to: null,
  };

  it("reads min and max, blank max meaning no limit", () => {
    const checked = checkGroup({
      name: "Extras",
      min_select: "0",
      max_select: "",
      available_from: "",
      available_to: "",
    });
    expect(checked).toEqual({
      value: {
        name: "Extras",
        min_select: 0,
        max_select: null,
        available_from: null,
        available_to: null,
      },
    });
  });

  it("refuses a max below the min", () => {
    const checked = checkGroup({
      name: "Extras",
      min_select: "2",
      max_select: "1",
      available_from: "",
      available_to: "",
    });
    expect(checked).toHaveProperty("errors.max_select");
  });

  it("patches a group's changed fields only", () => {
    const next = { name: "Spice level", min_select: 1, max_select: 2, available_from: null, available_to: null };
    expect(groupPatch(group, next)).toEqual({ max_select: 2 });
  });

  it("describes a group's selection rule", () => {
    expect(selectionLabel(1, 1)).toBe("Pick 1");
    expect(selectionLabel(0, 3)).toBe("Optional, up to 3");
    expect(selectionLabel(1, 2)).toBe("Pick 1 to 2");
    expect(selectionLabel(0, null)).toBe("Optional, any number");
  });

  it("lets a modifier take money off", () => {
    expect(checkModifier({ name: "No rice", price: "-1.00", kitchen_name: "" })).toEqual({
      value: { name: "No rice", price_delta_cents: -100, kitchen_name: null },
    });
    expect(checkModifier({ name: "", price: "x", kitchen_name: "" })).toEqual({
      errors: { name: expect.any(String), price: expect.any(String) },
    });
  });

  it("patches a modifier's changed fields only", () => {
    const butter = { id: "M", group_id: "G", name: "Butter", kitchen_name: null, price_delta_cents: 100 };
    expect(modifierPatch(butter, { name: "Butter", price_delta_cents: 150, kitchen_name: null })).toEqual({
      price_delta_cents: 150,
    });
  });
});

describe("staff", () => {
  it("wants a 4 to 8 digit PIN", () => {
    expect(pinError("1234")).toBeNull();
    expect(pinError("12345678")).toBeNull();
    for (const bad of ["123", "123456789", "12a4", " 1234"]) expect(pinError(bad)).not.toBeNull();
  });

  it("checks a new person", () => {
    expect(checkStaff({ name: " Priya ", role: "manager", pin: "4321" })).toEqual({
      value: { name: "Priya", role: "manager", pin: "4321" },
    });
    expect(checkStaff({ name: "", role: "staff", pin: "12" })).toEqual({
      errors: { name: expect.any(String), pin: expect.any(String) },
    });
  });
});

describe("tills", () => {
  const till = (start: number, end: number) =>
    ({ order_number_start: start, order_number_end: end }) as DeviceOut;

  it("suggests the next free block of order numbers", () => {
    expect(suggestRange([])).toEqual({ start: 1000, end: 1999 });
    expect(suggestRange([till(1000, 1999)])).toEqual({ start: 2000, end: 2999 });
    expect(suggestRange([till(2000, 2999), till(1000, 1999)])).toEqual({ start: 3000, end: 3999 });
  });

  it("checks a new till", () => {
    expect(checkDevice({ name: "Till 2", start: "2000", end: "2999" })).toEqual({
      value: { name: "Till 2", order_number_start: 2000, order_number_end: 2999 },
    });
    expect(checkDevice({ name: "", start: "3000", end: "2000" })).toEqual({
      errors: { name: expect.any(String), end: expect.any(String) },
    });
    expect(checkDevice({ name: "T", start: "0", end: "1.5" })).toEqual({
      errors: { start: expect.any(String), end: expect.any(String) },
    });
  });
});
