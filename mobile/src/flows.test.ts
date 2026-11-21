import { ApiError } from "./lib/api";
import type { OrderSummary, PaymentOut } from "./lib/types";
import {
  cardSheetError,
  cleanPin,
  discountFrom,
  holidaySurcharge,
  mergeOpenOrders,
  payAmount,
  refundFrom,
  tipAmount,
  validPin,
  withApproval,
} from "./flows";
import { timedFetch } from "./net";
import { dosa, placed } from "./offline/testkit";

describe("manager approval", () => {
  it("tries without a PIN, then asks, and says when a PIN was wrong", async () => {
    const tried: (string | undefined)[] = [];
    const asked: (string | undefined)[] = [];
    const answers = ["1111", "4321"];
    const result = await withApproval(
      async (error) => {
        asked.push(error);
        return answers.shift() ?? null;
      },
      async (pin) => {
        tried.push(pin);
        if (pin === undefined) throw new ApiError(403, "manager_pin_required", "a manager's PIN is needed");
        if (pin !== "4321") throw new ApiError(403, "wrong_pin", "that PIN isn't a manager's");
        return "voided";
      },
    );
    expect(result).toBe("voided");
    expect(tried).toEqual([undefined, "1111", "4321"]);
    expect(asked).toEqual([undefined, "That PIN isn't a manager's."]);
  });

  it("stops when the manager cancels, and passes other errors on", async () => {
    const needPin = async () => {
      throw new ApiError(403, "manager_pin_required", "x");
    };
    await expect(withApproval(async () => null, needPin)).resolves.toBeNull();
    const boom = new ApiError(409, "order_closed", "closed");
    await expect(
      withApproval(async () => "1234", async () => {
        throw boom;
      }),
    ).rejects.toBe(boom);
  });

  it("takes 4 to 8 digits", () => {
    expect([validPin("123"), validPin("1234"), validPin("12345678"), validPin("12a4")]).toEqual([false, true, true, false]);
    expect(cleanPin(" 12-34 5678 9")).toBe("12345678");
  });
});

describe("discounts and surcharges", () => {
  it("builds a percentage or dollar discount, with an optional reason", () => {
    expect(discountFrom(1000, "", " Staff meal ")).toEqual({ kind: "discount", basis: "percent", value: 1000, label: "Staff meal" });
    expect(discountFrom(null, "5.50", "")).toEqual({ kind: "discount", basis: "amount", value: 550, label: null });
    expect(discountFrom(null, "0", "")).toHaveProperty("error");
    expect(discountFrom(null, "lots", "")).toHaveProperty("error");
  });

  it("offers the venue's public-holiday surcharge", () => {
    expect(holidaySurcharge(1500)).toEqual({ kind: "surcharge", basis: "percent", value: 1500, label: "Public holiday" });
  });
});

describe("refunds", () => {
  const cash: PaymentOut = { payment_id: "P1", provider: "cash", external_ref: null, amount_cents: 2000, tip_cents: 0, currency: "AUD", status: "captured" };
  const card: PaymentOut = { ...cash, payment_id: "P2", provider: "external", external_ref: "AUTH 1" };

  it("gives back no more than is left, with a reason", () => {
    expect(refundFrom({ payment: cash, left: 1500 }, "15.00", " Cold food ", "")).toEqual({
      paymentId: "P1",
      amountCents: 1500,
      reason: "Cold food",
      externalRef: null,
    });
    expect(refundFrom({ payment: cash, left: 1500 }, "15.01", "x", "")).toEqual({ error: "At most $15.00 can go back." });
    expect(refundFrom({ payment: cash, left: 1500 }, "5", " ", "")).toHaveProperty("error");
    expect(refundFrom({ payment: cash, left: 1500 }, "0", "x", "")).toHaveProperty("error");
  });

  it("needs the terminal's reference for a card-on-terminal refund", () => {
    expect(refundFrom({ payment: card, left: 2000 }, "20", "x", " ")).toHaveProperty("error");
    expect(refundFrom({ payment: card, left: 2000 }, "20", "x", " RF 9 ")).toMatchObject({ externalRef: "RF 9" });
  });
});

describe("taking payment", () => {
  it("checks the amount against what's owing", () => {
    expect(payAmount("12.50", 2000)).toEqual({ cents: 1250 });
    expect(payAmount("20.01", 2000)).toEqual({ error: "That's more than the $20.00 owing." });
    expect(payAmount("0", 2000)).toEqual({ error: "Enter an amount above zero." });
    expect(payAmount("0", 0)).toEqual({ cents: 0 });
  });

  it("works out a tip as a share or a typed amount", () => {
    expect(tipAmount(2000, 1000, "")).toEqual({ cents: 200 });
    expect(tipAmount(2000, 0, "")).toEqual({ cents: 0 });
    expect(tipAmount(2000, null, "3.50")).toEqual({ cents: 350 });
    expect(tipAmount(2000, null, "")).toEqual({ cents: 0 });
    expect(tipAmount(2000, null, "x")).toEqual({ error: "Enter a tip like 5.00." });
  });

  it("stays quiet when the customer cancels the card sheet", () => {
    expect(cardSheetError(undefined)).toBeNull();
    expect(cardSheetError({ code: "Canceled", message: "The payment flow has been canceled" })).toBeNull();
    expect(cardSheetError({ code: "Failed", message: "Your card was declined." })).toBe("Your card was declined.");
    expect(cardSheetError({ code: "Failed" })).toBe("The card was declined.");
  });
});

describe("open orders", () => {
  const summary = (id: string, state = "open", total = 1000): OrderSummary => ({
    id,
    order_number: Number(id.slice(1)),
    state,
    fulfillment: "dine_in",
    table_label: null,
    business_date: "2026-11-21",
    created_at: "2026-11-21T01:00:00Z",
    subtotal_cents: total,
    total_cents: total,
    gst_cents: 0,
    balance_cents: total,
    tips_cents: 0,
    refunded_cents: 0,
    unsent_lines: 0,
  });

  it("shows this till's queued orders alongside the server's, newest first", () => {
    const queuedNew = placed(1, "O3", [dosa("L1")]).order;
    const queuedChange = { ...placed(2, "O1", [dosa("L2"), dosa("L3")]).order, state: "paid" };
    const stale = placed(3, "O0", [dosa("L4")]).order;
    const merged = mergeOpenOrders([summary("O2"), summary("O1")], [queuedNew, queuedChange, stale], new Set(["O3", "O1"]));
    expect(merged.map((o) => o.id)).toEqual(["O3", "O2"]);
  });
});

describe("requests give up on a server that has gone away", () => {
  it("aborts after the time limit", async () => {
    const hang = (_input: string, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => reject(new Error("aborted")));
      });
    await expect(timedFetch(10, hang)("http://x")).rejects.toThrow("aborted");
  });
});
