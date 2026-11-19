import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { createOnlineApi } from "./api";
import type { PlaceWebOrderRequest } from "./types";

function fakeFetch(respond: (url: string, init?: RequestInit) => Response) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const impl = async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    return respond(url, init);
  };
  return { impl, calls };
}

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("storefront api client", () => {
  it("prefixes /online and posts the order body", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { order: { id: "O1" } }));
    const api = createOnlineApi(impl, () => "http://venue.local:8000");
    const body = { order_id: "O1", lines: [] } as unknown as PlaceWebOrderRequest;
    await api.placeOrder("LOC", body);
    expect(calls[0].url).toBe("http://venue.local:8000/online/locations/LOC/orders");
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual(body);
  });

  it("reads the storefront on the same-origin proxy by default", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { location_id: "LOC" }));
    await createOnlineApi(impl).storefront("LOC");
    expect(calls[0].url).toBe("/api/online/locations/LOC");
  });

  it("turns a server error into an ApiError with its code", async () => {
    const { impl } = fakeFetch(() => json(409, { detail: "sold out", code: "item_unavailable" }));
    const err = await createOnlineApi(impl)
      .placeOrder("LOC", {} as PlaceWebOrderRequest)
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code]).toEqual([409, "item_unavailable"]);
  });

  it("reports an unreachable server as status 0", async () => {
    const api = createOnlineApi(async () => {
      throw new TypeError("Failed to fetch");
    });
    const err = await api.storefront("LOC").catch((e) => e);
    expect([err.status, err.code]).toEqual([0, "network_error"]);
  });
});
