# Voice AI order intake

How a phone call becomes an order in the pipeline, and where the external voice stack plugs
in. This is the **open-core, POS side** of voice ordering: resolving a spoken order against
the menu, confirming it, and placing it on the `voice` channel. The telephony and the LLM
are **not here** — they live in the separate voice stack (`livekit-impl` / `cold-caller`),
which is commercial and out of this repo.

## The seam

```
  caller ──phone──▶  voice stack  ──VoiceOrderDraft──▶  Void POS  ──▶ voice channel
 (speech)          (LiveKit + LLM)                     (this repo)      in the pipeline
                        ▲                                   │
                        └────────  VoiceOrderConfirmation ──┘  (read back to the caller)
```

`src/void_pos/voice/agent.py` is the whole contract between the two sides:

- **`VoiceAgent`** — a `Protocol` the voice stack implements. The POS only ever calls two
  methods on it: `take_order(call)` (listen, return structure) and
  `confirm(call, confirmation)` (read the priced order back, return a yes/no). No speech or
  LLM call is made from this repo.
- **`VoiceOrderDraft`** — what the stack hands in: items and modifiers **by name** (what the
  caller said) or **by menu id** (already matched), plus pickup/delivery and the caller's
  phone. Still unverified against the menu.
- **`VoiceOrderConfirmation`** — what the POS hands back: the resolved, priced order, with a
  `read_back` string for the stack to speak. Prices and GST are the venue's own, snapshotted
  from the live menu — so when the caller says "yes", the order placed is exactly this.

Tests supply a fake `VoiceAgent`; the shipped `ScriptedVoiceAgent` replays a fixed draft so
the CLI can drive the whole flow with no telephony.

## The flow (`src/void_pos/voice/service.py`)

1. `take_order` → the stack returns a `VoiceOrderDraft`.
2. `confirm_voice_order` → resolve names to menu items/modifiers, price it, and run the
   **same live-menu checks a till order gets** (`orders/rules.check_order_lines`: item on
   sale, modifiers allowed and in window, required min / allowed max selections). **Writes
   nothing** — this is the confirm-back step.
3. `confirm` → the stack reads `confirmation.read_back` to the caller.
4. On a yes, `place_voice_order` → capture the caller as a CRM customer (match by phone
   within the org, create if new), then place the order through the **one shared
   `place_order` path** on the `voice` channel. On a no, nothing is placed.

`run_voice_call(session, agent, call)` runs all four against a `VoiceAgent`.

## Why a "voice device"

Every order needs a device — a device owns a private order-number range and a per-device
sequence, which together make writes idempotent and collision-free. A phone order has no
paired till, so each location gets one server-side **voice device**
(`src/void_pos/voice/device.py`), created lazily on the first voice order. It holds no
token (nothing outside the server can authenticate as it) and uses a reserved order-number
band (`1_900_000_000`–`1_999_999_999`), well above any till's and just below the
online-ordering "web device" band so the two channels never share numbers. Its row is
locked (`for_update`) while a voice order allocates its number and sequence, so two
concurrent calls can't collide.

Idempotency: pass a caller-stable `order_id` on the draft (a ULID the stack mints once per
call) and a retry of the same order replays to the first result instead of placing twice.

## HTTP endpoints (`src/void_pos/api/voice_routes.py`)

Gated by `VOIDPOS_VOICE_ORDERING_ENABLED` (default on; off → `404`).

| Method | Path | Does |
|---|---|---|
| `POST` | `/voice/locations/{location_id}/quote` | Resolve + price for the read-back. Places nothing. |
| `POST` | `/voice/locations/{location_id}/orders` | Place the confirmed order on the `voice` channel. |

Like the online storefront, these are **channel-intake** routes gated by a feature flag
rather than a till token: the voice stack is a trusted server-side integration and, in a
deployment, reaches this API over the venue's private network / behind a gateway. Every
request is validated against the live menu regardless. A commercial build can put a
dedicated credential in front of these routes.

## CLI

Simulate an inbound call end to end, no telephony (uses `ScriptedVoiceAgent`):

```
void-pos voice-order --location <id> --phone "+61400000000" --name "Sam" \
    --item "Masala Dosa:2:Extra hot" --item "Mango Lassi"
```

`--item` is `NAME[:QTY[:MOD,MOD,...]]`, repeatable. `--fulfillment delivery --address "..."`
for delivery; `--decline` to have the caller say no at the read-back (nothing is placed).

## Scope / known limits (open core)

- The telephony and LLM are the external stack's concern; this repo mocks them at the
  `VoiceAgent` seam.
- The order records the `voice` channel, pickup/delivery, the caller (as a customer) and the
  lines. Persisting a delivery **address** / kitchen **note** on the order is deferred — those
  `Order` columns arrive with the online-ordering commercial module; here the address and
  note are carried in the confirmation read-back only.
- Loyalty earn/redeem on voice orders is out of scope (a commercial concern), as it is for
  the till path.
