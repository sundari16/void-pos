import { describe, expect, it } from "vitest";
import { recallCustomer, rememberCustomer, type RememberedCustomer } from "./store";

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k) => map.get(k) ?? null,
    setItem: (k, v) => void map.set(k, v),
    removeItem: (k) => void map.delete(k),
    clear: () => map.clear(),
    key: (i) => [...map.keys()][i] ?? null,
    get length() {
      return map.size;
    },
  } as Storage;
}

const ADA: RememberedCustomer = {
  customer_id: "C1",
  name: "Ada",
  phone: "+61400000000",
  email: null,
  marketing_opt_in: true,
};

describe("remembered customer", () => {
  it("round-trips per location", () => {
    const store = memoryStorage();
    expect(recallCustomer("LOC", store)).toBeNull();
    rememberCustomer("LOC", ADA, store);
    expect(recallCustomer("LOC", store)).toEqual(ADA);
    expect(recallCustomer("OTHER", store)).toBeNull(); // keyed by location
  });

  it("returns null on corrupt data instead of throwing", () => {
    const store = memoryStorage();
    store.setItem("voidpos.online.customer.LOC", "{not json");
    expect(recallCustomer("LOC", store)).toBeNull();
  });

  it("is a no-op when storage is unavailable", () => {
    expect(() => rememberCustomer("LOC", ADA, undefined)).not.toThrow();
    expect(recallCustomer("LOC", undefined)).toBeNull();
  });
});
