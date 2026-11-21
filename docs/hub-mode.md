# Hub mode: one till relaying for the others (design note)

Status: **design only, not built.** The mobile till already keeps working on its own when it
loses the server (on-device outbox, `mobile/src/offline`). Hub mode is the next layer: when
the venue loses the internet, one device on the venue's network relays between the tills so
they still see each other's orders and the kitchen still gets its tickets.

## Why it's needed

The target topology (architecture.md, decided 18 Nov 2026) puts the system of record in the
cloud. When the venue's internet drops, every till falls back to its own outbox:

- **Tills go blind to each other.** Table 7 opened on till A can't be paid on till B; B only
  has what it pulled before the drop.
- **The kitchen goes quiet.** Kitchen sends are queued on the till and print when the server
  is back (the outbox replays them through the send-to-kitchen route), not when the cashier
  taps Send.
- **Conflicts pile up.** Two tills paying the same order offline both succeed locally; the
  server keeps both payments and the tills flag it on sync, but only after the fact.

Self-hosted venues (the open core and the Madras Curry House pilot) have the server on the
LAN, so an internet drop doesn't matter there; for them hub mode only matters if the venue
machine itself goes down.

## Shape

**One device is the hub.** In the first version a manager turns it on ("Act as hub") on a
till that stays on charge; electing one automatically comes later. The hub runs a small HTTP
server on the LAN that speaks the **same `/sync/push` and `/sync/pull` contracts** as the
cloud, so till code doesn't change: a till tries the cloud, then the hub, then its own
outbox.

**The hub relays; it never forwards.** The cloud only accepts a till's own events
(`foreign_event`), so every till keeps its events in its own outbox until the *cloud* acks
them, exactly as today. The hub only gives the tills a shared, live view while the cloud is
away:

1. A till pushes to the hub. The hub validates and folds each event with the same projection
   the till uses (`mobile/src/offline/projection.ts`, the port of `events/apply.py`), stores
   it, and gives it a hub-local sequence number.
2. Other tills pull from the hub by that sequence, as they pull from the cloud.
3. When the cloud is back, each till drains its own outbox to the cloud as it does now. The
   hub's log is discarded once every till it served has drained (it never held the only
   copy of anything).

**The hub prints.** It's on the LAN, so it can open the kitchen printer (TCP 9100) when it
receives an `order.sent`. The till then marks that outbox entry as printed, so on reconnect
it pushes the `order.sent` event through `/sync/push` instead of replaying the
send-to-kitchen route (which would print the ticket a second time). This needs the ESC/POS
ticket builder from `src/void_pos/printing` in TypeScript, which is also what direct printing
from a single till needs.

**Conflicts.** The cloud stays the referee: it applies each batch in canonical
`(occurred_at, event_id)` order and its order state machine settles clashes (first terminal
change wins; payments are always kept for reconciliation). The hub cuts conflicts down
because the tills see each other's changes within seconds, and it can refuse the obvious
ones locally (adding to an order another till has paid).

## Open questions

- **Running a server on a phone or tablet.** React Native has no HTTP server; it needs a
  native module (a small Expo module over the platform's socket APIs), which means a custom
  dev build rather than Expo Go. Alternative: the hub is a tiny always-on box (a Raspberry Pi
  running the same Node code), which also suits printing.
- **Finding the hub.** mDNS/zeroconf (`_voidpos._tcp`) to start with, a typed address as the
  fallback (the till already lets you type the server's address).
- **Trust on the LAN.** The hub can't check a cloud bearer token offline. The cloud would
  hand each till a per-location hub key at pairing (`/devices/me`), and tills sign their
  requests to the hub with it.
- **Manager approvals offline.** Voids, discounts, refunds and taking off sent items need a
  manager's PIN, which only the server checks today. Offline approval needs signed
  approvals (architecture.md, "Who may do what") or PIN hashes held on the hub.
- **Card payments.** Stripe needs the internet regardless; card-on-terminal and cash work
  offline already.

## Follow-ups

1. ESC/POS ticket builder in TypeScript; direct printing from a single till (also unblocks
   the hub printing).
2. The hub server (native module or always-on box), LAN discovery and the per-location hub
   key.
3. Tills: try cloud, then hub, then outbox; mark hub-printed kitchen sends.
4. Signed manager approvals so voids and discounts can be approved offline.
