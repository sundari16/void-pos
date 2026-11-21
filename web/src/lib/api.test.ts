import { describe, expect, it } from "vitest";
import { ApiError, createApi } from "./api";
import { apiBase } from "./config";

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

describe("api client", () => {
  it("sends the bearer token and query", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { ok: true }));
    const api = createApi(impl, () => "http://pos.local:8000");
    await api.closeout("tok", "LOC", "2026-11-18");
    expect(calls[0].url).toBe("http://pos.local:8000/reports/LOC/closeout?business_date=2026-11-18");
    expect((calls[0].init?.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("turns the server's error body into an ApiError with its code and fields", async () => {
    const { impl } = fakeFetch(() =>
      json(409, { detail: "seq 3 is already used", code: "seq_conflict", last_seq: 3 }),
    );
    const err = await createApi(impl, () => "x")
      .placeOrder("tok", {} as never)
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect([err.status, err.code, err.body.last_seq]).toEqual([409, "seq_conflict", 3]);
  });

  it("copes with a non-JSON error", async () => {
    const { impl } = fakeFetch(() => new Response("Bad Gateway", { status: 502 }));
    const err = await createApi(impl, () => "x").me("tok").catch((e) => e);
    expect([err.status, err.code]).toEqual([502, "http_502"]);
  });

  it("pushes and pulls the device sync log", async () => {
    const { impl, calls } = fakeFetch(() => json(200, { acks: [], events: [], has_more: false }));
    const api = createApi(impl, () => "http://pos.local:8000");
    await api.syncPull("tok", null);
    await api.syncPull("tok", "42", 100);
    await api.syncPush("tok", []);
    expect(calls.map((c) => c.url)).toEqual([
      "http://pos.local:8000/sync/pull?limit=500",
      "http://pos.local:8000/sync/pull?cursor=42&limit=100",
      "http://pos.local:8000/sync/push",
    ]);
    expect(calls[2].init?.body).toBe(JSON.stringify({ events: [] }));
  });

  it("reports an unreachable server as status 0", async () => {
    const api = createApi(async () => {
      throw new TypeError("Failed to fetch");
    }, () => "http://pos.local:8000");
    const err = await api.me("tok").catch((e) => e);
    expect([err.status, err.code]).toEqual([0, "network_error"]);
  });
});

describe("apiBase", () => {
  it("defaults to the same-origin /api proxy", () => {
    expect(apiBase("")).toBe("/api");
  });

  it("honours an explicit API address", () => {
    expect(apiBase("http://192.168.1.20:8000/")).toBe("http://192.168.1.20:8000");
  });
});
