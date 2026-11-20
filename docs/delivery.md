# On-demand delivery dispatch

Send a courier for the venue's **own** delivery orders — orders taken on the till, by
phone/voice, or through the venue's own web storefront — so a venue can offer delivery
without hiring drivers. This is distinct from delivery-aggregator orders (Uber Eats,
DoorDash, Menulog): those arrive already carrying the platform's own courier and are never
dispatched here.

The open core ships the seam and a fully working **mock courier**. The real **Uber Direct**
adapter needs a merchant's partner credentials (customer id, OAuth client, signed-webhook
secret) and is a commercial module — the same open-core/commercial split as payments
(`CashProvider` in the core, Stripe Connect commercial).

## The seam

`void_pos.delivery.DeliveryProvider` (a `Protocol`) has three methods, all partner-neutral:

| Method | Purpose |
|---|---|
| `request_delivery(DeliveryRequest) -> DeliveryResult` | Ask for a courier: pickup = the venue, dropoff = the customer. `delivery_id` is our ULID and the provider's idempotency key. |
| `get_status(external_ref) -> DeliveryResult` | Poll the platform for a job's current status. |
| `cancel(external_ref) -> DeliveryResult` | Call off a job (addressed by the platform's `external_ref`; stateless across our requests). |

`DeliveryRequest` carries the `delivery_id`, the order's number (for the courier's bag
label), the `pickup`/`dropoff` parties (name, phone, address, optional notes/coordinates)
and the declared order value. `DeliveryResult` carries the provider name, the normalized
status, the platform's `external_ref`, the fee in cents, the tracking URL and the courier's
name.

### Providers

- **`MockCourier`** (open core) — an in-memory courier with no network. `request_delivery`
  is idempotent on `delivery_id`; `advance(external_ref, status)` stands in for the
  platform's status callbacks in tests and demos. `build_provider("mock")` returns it.
- **`uber_direct`** — commercial. `build_provider("uber_direct")` raises `commercial_provider`
  in the open core; `uber_direct.py` documents exactly what the commercial adapter maps onto
  Uber's `/deliveries` endpoints, its webhook, and its cancel call.

## The record

Each courier job is one `delivery` row, keyed on our ULID — never on the courier's id (which
lives in `external_ref`), so a venue can move between courier platforms without rewriting
history. It carries `location_id` and `business_date`, the fee in integer cents (AUD), the
dropoff snapshot taken at dispatch, and the lifecycle stamps. A delivery is **not** projected
from the event log: like an aggregator tie row, it mirrors the state of an external job whose
statuses we don't control, rather than a fact the venue owns.

### Status lifecycle

```
requested ─▶ accepted ─▶ picked_up ─▶ delivered        (terminal)
     │           │
     └─── canceled (before pickup) ───┘
   any active status ─▶ failed                          (terminal)
```

A provider may skip intermediate states (a courier assigned and collecting before the
`accepted` callback lands). Repeating the current status is an idempotent no-op; a move the
machine forbids is refused (`bad_delivery_transition`). Cancelling is allowed only while the
status is `requested` or `accepted` — once the courier has the food it is `too_late_to_cancel`.

## HTTP

| Method & path | Who | What |
|---|---|---|
| `POST /orders/{order_id}/delivery` | paired till | Dispatch a courier. Body: `delivery_id`, `dropoff_name`, `dropoff_phone`, `dropoff_address`, optional `dropoff_notes`, `order_value_cents`. Idempotent on `delivery_id`. |
| `GET /orders/{order_id}/delivery` | till / admin | The order's courier job. |
| `GET /deliveries/{delivery_id}` | till / admin | A job by id. |
| `POST /deliveries/{delivery_id}/status` | admin | Record a status the platform reported. In the open core the admin token (or the CLI) drives it; a real adapter calls it from a signature-verified webhook. |
| `POST /deliveries/{delivery_id}/cancel` | paired till | Cancel, before pickup. |

`VOIDPOS_DELIVERY_ENABLED=false` turns the surface off (503 `delivery_disabled`).
`VOIDPOS_DELIVERY_PROVIDER` selects the courier (default `mock`).

The venue's pickup address comes from the location (set it with `void-pos venue-details
--address ...`); dispatch is refused with `venue_address_missing` until it is set.

## CLI

```
# Dispatch a courier for a delivery order (uses the mock courier by default)
void-pos delivery-dispatch --location <id> --order <id> \
    --name "Priya" --phone "+61400000000" --address "12 Example St, Brisbane" \
    [--notes "Buzzer 4"] [--provider mock] [--delivery-id <ulid>]

# Simulate a courier status callback
void-pos delivery-status --delivery <id> --status picked_up [--courier "Sam"] [--fee-cents 799]
```

## Notes for extending this

- **Dropoff address.** The open core takes the dropoff on the dispatch request and snapshots
  it onto the `delivery` row, so the feature stands alone. When an `Order.delivery_address`
  field lands (the web storefront work), the dispatch endpoint can default the dropoff from
  the order instead of requiring it in the body.
- **The fee** is recorded on the delivery (integer cents) but is not added to the order total
  here — whether the venue absorbs it or charges the customer (a surcharge line) is a venue
  policy decision left to a later iteration.
- **A real adapter** maps the platform's status vocabulary onto `void_pos.delivery.states`
  and drives the same `POST /deliveries/{id}/status` path the mock uses from its verified
  webhook, so the courier-callback path is identical whichever provider is live.
