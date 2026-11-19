import { ApiError } from "./api";
import { isRetryable, runCommand, type Pending } from "./commands";
import type { CommandIn } from "./types";

type Body = CommandIn;

function pending(): Pending<Body> {
  return { current: null };
}

describe("runCommand", () => {
  it("clears the pending body once the server answers", async () => {
    const p = pending();
    let built = 0;
    const result = await runCommand<Body, { seq: number }>({
      pending: p,
      build: () => {
        built += 1;
        return { event_id: "e", seq: 9 };
      },
      send: async (body) => ({ seq: body.seq }),
      seqAfter: (last) => last + 1,
      onUnauthorized: () => {},
    });
    expect(result).toEqual({ seq: 9 });
    expect(built).toBe(1);
    expect(p.current).toBeNull();
  });

  it("keeps the pending body after a retryable failure, so a retry resends the same ids", async () => {
    const p = pending();
    await expect(
      runCommand<Body, unknown>({
        pending: p,
        build: () => ({ event_id: "e", seq: 1 }),
        send: async () => {
          throw new ApiError(0, "network_error", "offline");
        },
        seqAfter: (last) => last + 1,
        onUnauthorized: () => {},
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(p.current).not.toBeNull();
  });

  it("resyncs the seq and resends once on seq_conflict", async () => {
    const p = pending();
    let calls = 0;
    const result = await runCommand<Body, { seq: number }>({
      pending: p,
      build: () => ({ event_id: "e", seq: 2 }),
      send: async (body) => {
        calls += 1;
        if (calls === 1) throw new ApiError(409, "seq_conflict", "stale", { last_seq: 5 });
        return { seq: body.seq };
      },
      seqAfter: (last) => last + 1,
      onUnauthorized: () => {},
    });
    expect(result).toEqual({ seq: 6 });
    expect(p.current).toBeNull();
  });

  it("calls onUnauthorized and drops the body on 401", async () => {
    const p = pending();
    let unauthorized = false;
    await expect(
      runCommand<Body, unknown>({
        pending: p,
        build: () => ({ event_id: "e", seq: 1 }),
        send: async () => {
          throw new ApiError(401, "invalid_token", "revoked");
        },
        seqAfter: (last) => last + 1,
        onUnauthorized: () => {
          unauthorized = true;
        },
      }),
    ).rejects.toBeInstanceOf(ApiError);
    expect(unauthorized).toBe(true);
    expect(p.current).toBeNull();
  });
});

describe("isRetryable", () => {
  it("is true for a network error or a 5xx", () => {
    expect(isRetryable(new ApiError(0, "network_error", "x"))).toBe(true);
    expect(isRetryable(new ApiError(503, "unavailable", "x"))).toBe(true);
    expect(isRetryable(new ApiError(409, "seq_conflict", "x"))).toBe(false);
    expect(isRetryable(new Error("plain"))).toBe(false);
  });
});
