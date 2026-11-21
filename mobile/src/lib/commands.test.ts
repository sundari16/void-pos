// Generated from web/src/lib/commands.test.ts by mobile/scripts/sync-lib.mjs — edit the web copy.
import { ApiError } from "./api";
import { friendlyError, isRetryable, runCommand, type Pending } from "./commands";
import type { CommandIn } from "./types";

function setup(responses: (() => unknown)[]) {
  const pending: Pending<CommandIn> = { current: null };
  let built = 0;
  const sent: CommandIn[] = [];
  const onUnauthorized = jest.fn();
  const seqAfter = jest.fn((last: number) => last + 1);
  const opts = {
    pending,
    build: () => ({ event_id: `E${(built += 1)}`, seq: built }),
    send: async (body: CommandIn) => {
      sent.push(body);
      const next = responses.shift();
      if (!next) throw new Error("no response queued");
      return next();
    },
    seqAfter,
    onUnauthorized,
  };
  return { opts, pending, sent, onUnauthorized, seqAfter };
}

const fail = (err: ApiError) => () => {
  throw err;
};

describe("runCommand", () => {
  it("clears the pending body once the server answers", async () => {
    const { opts, pending } = setup([() => "ok"]);
    await expect(runCommand(opts)).resolves.toBe("ok");
    expect(pending.current).toBeNull();
  });

  it("keeps the same ids after a dropped connection, so the retry replays", async () => {
    const { opts, pending, sent } = setup([
      fail(new ApiError(0, "network_error", "offline")),
      () => "ok",
    ]);
    await expect(runCommand(opts)).rejects.toThrow(ApiError);
    expect(pending.current).not.toBeNull();
    await runCommand(opts);
    expect(sent[0]).toEqual(sent[1]);
  });

  it("keeps the ids after a 5xx (printer down) too", async () => {
    const { opts, pending } = setup([fail(new ApiError(503, "printer_unavailable", "down"))]);
    await expect(runCommand(opts)).rejects.toThrow();
    expect(pending.current?.event_id).toBe("E1");
  });

  it("drops the body after a final 4xx", async () => {
    const { opts, pending } = setup([fail(new ApiError(409, "order_closed", "paid"))]);
    await expect(runCommand(opts)).rejects.toThrow();
    expect(pending.current).toBeNull();
  });

  it("resyncs once on a seq conflict, keeping the event id", async () => {
    const { opts, sent, seqAfter } = setup([
      fail(new ApiError(409, "seq_conflict", "used", { last_seq: 41 })),
      () => "ok",
    ]);
    await expect(runCommand(opts)).resolves.toBe("ok");
    expect(seqAfter).toHaveBeenCalledWith(41);
    expect(sent[1]).toEqual({ event_id: sent[0].event_id, seq: 42 });
  });

  it("lets a command repair its own body once (a reused order number)", async () => {
    const { opts, sent } = setup([
      fail(new ApiError(409, "order_number_used", "taken", { next_order_number: 1005 })),
      () => "ok",
    ]);
    const repair = jest.fn((err: ApiError, body: CommandIn) =>
      err.code === "order_number_used" ? { ...body, seq: 99 } : null,
    );
    await expect(runCommand({ ...opts, repair })).resolves.toBe("ok");
    expect(repair).toHaveBeenCalledTimes(1);
    expect(sent[1]).toEqual({ event_id: sent[0].event_id, seq: 99 });
  });

  it("gives up after a second seq conflict", async () => {
    const conflict = new ApiError(409, "seq_conflict", "used", { last_seq: 41 });
    const { opts } = setup([fail(conflict), fail(conflict)]);
    await expect(runCommand(opts)).rejects.toBe(conflict);
  });

  it("reports a revoked token", async () => {
    const { opts, onUnauthorized } = setup([fail(new ApiError(401, "invalid_token", "no"))]);
    await expect(runCommand(opts)).rejects.toThrow();
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });
});

describe("errors", () => {
  it("classifies retryable failures", () => {
    expect(isRetryable(new ApiError(0, "network_error", ""))).toBe(true);
    expect(isRetryable(new ApiError(503, "printer_unavailable", ""))).toBe(true);
    expect(isRetryable(new ApiError(422, "invalid_request", ""))).toBe(false);
    expect(isRetryable(new Error("boom"))).toBe(false);
  });

  it("explains known codes and passes the server's words through otherwise", () => {
    expect(friendlyError(new ApiError(503, "printer_unavailable", "x"))).toMatch(/printer/);
    expect(friendlyError(new ApiError(422, "amount_exceeds_balance", "more than owing"))).toBe(
      "more than owing",
    );
  });
});
