import { describe, expect, it } from "vitest";
import { createAdminApi, errorDetail } from "./adminApi";
import { ApiError } from "./api";

function fakeFetch(respond: (url: string, init?: RequestInit) => Response | Promise<Response>) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const impl = async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return respond(url, init);
  };
  return { impl, calls };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const headers = (init?: RequestInit) => init?.headers as Record<string, string>;

describe("admin api client", () => {
  it("sends the admin token on every call", async () => {
    const { impl, calls } = fakeFetch(() => json(200, []));
    await createAdminApi("admin-token", impl, () => "http://pos.local").locations();
    expect(calls[0].url).toBe("http://pos.local/admin/locations");
    expect(headers(calls[0].init).Authorization).toBe("Bearer admin-token");
  });

  it("patches with a JSON body", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { id: "ITEM1" }));
    await createAdminApi("t", impl, () => "").updateItem("ITEM1", { base_price_cents: 2000 });
    expect(calls[0].url).toBe("/menu/items/ITEM1");
    expect(calls[0].init?.method).toBe("PATCH");
    expect(headers(calls[0].init)["Content-Type"]).toBe("application/json");
    expect(calls[0].init?.body).toBe('{"base_price_cents":2000}');
  });

  it("repeats the state parameter for each state", async () => {
    const { impl, calls } = fakeFetch(() => json(200, []));
    await createAdminApi("t", impl, () => "").orders("LOC", "2026-11-19", ["open", "paid"]);
    expect(calls[0].url).toBe(
      "/locations/LOC/orders?business_date=2026-11-19&state=open&state=paid&limit=500",
    );
  });

  it("returns nothing for a 204", async () => {
    const { impl, calls } = fakeFetch(() => new Response(null, { status: 204 }));
    await expect(createAdminApi("t", impl, () => "").deleteItem("ITEM1")).resolves.toBeUndefined();
    expect(calls[0].init?.method).toBe("DELETE");
  });

  it("turns a refusal into an ApiError with the server's detail and fields", async () => {
    const { impl } = fakeFetch(() =>
      json(409, {
        detail: "change #7 to this item is newer; revert it first",
        code: "changed_since",
        later_change: 7,
      }),
    );
    const err = await createAdminApi("t", impl, () => "").revertChange("C1").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code, err.detail, err.body.later_change]).toEqual([
      409,
      "changed_since",
      "change #7 to this item is newer; revert it first",
      7,
    ]);
  });

  it("reports an unreachable server as status 0", async () => {
    const api = createAdminApi("t", async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await api.locations().catch((e) => e);
    expect([err.status, err.code]).toEqual([0, "network_error"]);
  });

  it("shows the server's detail, or says the server is unreachable", () => {
    expect(errorDetail(new ApiError(422, "invalid_abn", "an ABN is 11 digits"))).toBe("an ABN is 11 digits");
    expect(errorDetail(new ApiError(0, "network_error", "x"))).toMatch(/Can't reach/);
    expect(errorDetail(new Error("boom"))).toBe("boom");
  });

  it("fetches the export as a file with the auth header", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { location: { id: "LOC" } }));
    const blob = await createAdminApi("t", impl, () => "").exportBlob("LOC");
    expect(calls[0].url).toBe("/export/LOC");
    expect(headers(calls[0].init).Authorization).toBe("Bearer t");
    expect(JSON.parse(await blob.text())).toEqual({ location: { id: "LOC" } });
  });
});
