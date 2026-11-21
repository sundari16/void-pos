// Shared fixtures for the offline tests (not used by the app).

import type { LineIn, OrderResponse, PlaceOrderRequest, SyncEvent } from "../lib/types";
import { placeEvent, type TillIdentity } from "./events";
import { applyEvent } from "./projection";

export const TILL: TillIdentity & { token: string } = {
  token: "tok",
  deviceId: "01JDEV00000000000000000001",
  locationId: "01JLOC00000000000000000001",
  timezone: "Australia/Brisbane",
};

/** Ids that sort in the order they're made, like the till's ULIDs. */
export function idMaker(prefix: string): () => string {
  let n = 0;
  return () => `${prefix}${String((n += 1)).padStart(26 - prefix.length, "0")}`;
}

export const dosa = (id: string, quantity = 1): LineIn => ({
  id,
  quantity,
  name_snapshot: "Masala Dosa",
  unit_price_cents: 1850,
  tax_rate_snapshot: 0.1,
  category_snapshot: "Mains",
});

export const ghee = (id: string, parent: string): LineIn => ({
  id,
  parent_line_id: parent,
  quantity: 1,
  name_snapshot: "Ghee roast",
  unit_price_cents: 150,
  tax_rate_snapshot: 0.1,
});

export function placeBody(seq: number, orderId: string, lines: LineIn[], orderNumber = 1001): PlaceOrderRequest {
  return {
    event_id: `E${String(seq).padStart(25, "0")}`,
    seq,
    occurred_at: `2026-11-21T0${Math.min(seq, 9)}:00:00.000Z`,
    order_id: orderId,
    order_number: orderNumber,
    channel: "pos",
    fulfillment: "dine_in",
    business_date: "2026-11-21",
    table_label: "4",
    lines,
  };
}

export function placed(seq: number, orderId: string, lines: LineIn[], orderNumber = 1001): {
  event: SyncEvent;
  order: OrderResponse;
} {
  const event = placeEvent(TILL, placeBody(seq, orderId, lines, orderNumber));
  return { event, order: applyEvent(null, event).order as OrderResponse };
}

export function cmd(seq: number) {
  return {
    event_id: `E${String(seq).padStart(25, "0")}`,
    seq,
    occurred_at: `2026-11-21T0${Math.min(seq, 9)}:30:00.000Z`,
  };
}
