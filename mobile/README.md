# Void POS — Mobile till

The in-person point-of-sale as a **React Native (Expo SDK 58)** app for iOS and Android. It
does what the web till does — pair, menu, order (items with modifier child lines), send to
the kitchen, **cash / card on the terminal / card on screen (Stripe)** with splits and tips,
**discounts and surcharges, taking items off, voids, refunds with a manager's PIN**, the
**open-orders picker** and the **shift closeout** — and it **keeps working when the server
can't be reached**: changes are saved on the device and sync through `/sync/push` and
`/sync/pull` once the server is back.

Money is **integer cents** end to end; amounts are only formatted to AUD for display.

> **Status:** feature-complete against the web till, offline-first. Typechecks, lints,
> 109 unit tests, `expo-doctor` clean, and the sync path is tested end to end against a live
> API (see [Testing](#testing)). Android: an installable APK is built in CI
> (`.github/workflows/mobile-android.yml`). iOS: not built (needs a Mac). See
> [Scope / known gaps](#scope--known-gaps).

## What's shared with the web till

The order, menu, payment and API rules live in `web/src/lib/` and are copied **verbatim**
into `src/lib/` by `npm run sync-lib`, with the web tests that cover them (vitest → jest).
CI runs `npm run check-lib`, so the copies can't drift: change the web file, then re-sync.

- `types.ts` — mirrors of the backend schemas (`src/void_pos/api/schemas.py`), sync events included.
- `ulid.ts` — device-minted ULIDs (idempotent writes; a retry resends the same ids).
- `menu.ts` — availability windows, modifier selection rules.
- `order.ts` — the draft order and its flattening to `LineIn`s (item line, then its modifier
  child lines via `parent_line_id`).
- `placed.ts` — a placed order grouped for display; applying server answers; refundable amounts.
- `payments.ts` — cash / card-on-terminal amounts, splits, typed dollars.
- `commands.ts` — the safe-retry wrapper (`runCommand`).
- `api.ts` — the typed API client (including `syncPush` / `syncPull`).

Mobile-only: `config.ts` (build-time default server), `money.ts` (hand-rolled AUD formatter;
Hermes `Intl` differs across platforms), `till.ts` + `storage.ts` (token and counters in
AsyncStorage), `server.ts` (the server address, changeable on the pairing screen),
`net.ts` (the API client with an 8 s request limit), `flows.ts` (the pure parts of the
approval, discount, refund, tip and open-orders flows), `stripeSheet.ts`, and `src/offline/`.

## Working offline

`src/offline/` is the till's local-first layer:

| File | What it does |
| --- | --- |
| `events.ts` | A till command as the event the online route would record (same payload, same ids), with the local refusals the server would make. |
| `projection.ts` | Folds events onto the till's copy of an order exactly as `src/void_pos/events/apply.py` does; idempotent by line / payment / adjustment / refund id. |
| `totals.ts` | The server's `order_math` (totals, discounts then surcharges, GST per rate, half-up rounding) with exact integer ratios. |
| `store.ts`, `sqliteStore.ts` | The outbox and the till's recent orders, in **expo-sqlite** (an in-memory fallback if SQLite can't open). |
| `sync.ts` | The sync engine: push the outbox, pull the other tills' changes, keep the cursor. |

How it behaves:

- Every command tries the server first. If the request **can't reach the server** (or the
  order already has changes waiting), the change is **saved on the till** with the ids it
  already minted, the order shows as the server will have it, and the till carries on. The
  top bar shows **Online / Offline · N saved / N to sync / N need attention**; tap it for detail
  and **Sync now**.
- **Offline, the till can**: place orders, add items, send to the kitchen (queued: the
  ticket prints when the server is back), take cash and card-on-terminal payments (with
  splits and tips), add the public-holiday surcharge, take off items the kitchen hasn't had,
  take off adjustments, and pick up any recent order it knows (its own, and other tills'
  pulled while online).
- **Needs the server**: anything a manager approves (voids, discounts, taking off items the
  kitchen has, refunds), and card on screen (Stripe). The buttons say so while offline.
- **Catching up** (every 8 s while offline, 20 s online, and when the app returns to the
  foreground): pull first (it doubles as the reachability check), then push. Placements go
  first, so an order is on the server before anything that changes it; kitchen sends are
  replayed through `POST /orders/{id}/send-to-kitchen` so they print; everything else goes
  through `/sync/push` in the order it happened. Each order with nothing left to send is
  then refreshed from the server.
- **Retry-safe.** An event that already reached the server (the response was lost) comes
  back `duplicate`. A `seq_conflict` or `order_number_used` takes fresh counters from
  `/devices/me` and pushes again once. The till's event clock never steps backwards (the
  server applies a batch in `occurred_at` order), and on start the counters move past
  anything already queued.
- **Refusals are kept, not dropped.** A refused event stays in the outbox marked *failed*
  (shown under **Refused by the server** with a Discard button); if a placement is refused,
  that order's later changes are held back rather than pushed to an order the server
  doesn't have. Changes that lost to another till (`conflict`) are reported, and a payment
  taken offline on an order another till had already settled is flagged: the server keeps
  the money, so check it and refund if it was paid twice.
- The store belongs to one pairing: pairing as a different till starts it afresh. Unpairing
  keeps anything still queued (it goes once the device is paired again as the same till).

## Prerequisites

- Node 22 and npm
- The Void POS backend (see the repo root `README.md`)
- To run on a device: Expo Go, a development build, an Android emulator, or the APK from CI
  (expo-sqlite and Stripe's payment sheet are native modules; the versions here are the ones
  SDK 58 bundles)

## 1. Run the backend with seed data

From the **repo root**, in your Python venv (never `docker compose` from a worktree):

```bash
pip install -e ".[dev]"
python scripts/seed_dev.py      # one org / location / till + menu; prints a pairing code
VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app --host 0.0.0.0
```

On Windows PowerShell:

```powershell
$env:VOIDPOS_DATABASE_URL = "sqlite:///./void_pos_dev.db"
uvicorn void_pos.main:app --host 0.0.0.0
```

The seed script also creates a manager with PIN `1234` (dev data only), so voids, discounts
and refunds ask for it.

## 2. Point the app at the backend

The **pairing screen has a Server field**: type the API's address on the venue network
(`192.168.1.20:8000` is enough). It's kept on the device. The default comes from
`EXPO_PUBLIC_API_BASE` at build time (copy `.env.example` to `.env`), else
`http://localhost:8000`:

| Running on | Server |
| --- | --- |
| iOS simulator | `http://localhost:8000` |
| Android emulator | `http://10.0.2.2:8000` |
| Real device | `http://<your-computer-LAN-ip>:8000` |

Plain HTTP to the venue's own server is allowed (Android `usesCleartextTraffic`, iOS
`NSAllowsLocalNetworking`). The mobile till calls the API directly, so a non-localhost
address must be in the API's `VOIDPOS_CORS_ORIGINS` only for web builds; native apps aren't
subject to CORS.

## 3. Run the app and pair the till

```bash
cd mobile
npm ci
npm start           # Expo dev server; a (Android), i (iOS), or scan the QR
```

Enter the pairing code the seed script printed. Re-run the seed script for a fresh code;
each code works once.

## Card on screen (Stripe)

When the server has Stripe set up (`GET /payments/stripe` says enabled), the payment panel
shows **Card on screen**: the server starts a payment intent for the amount plus tip,
Stripe's own payment sheet takes the card (details go from the device straight to Stripe),
and the till records the payment (`method: "stripe"`), which is when the server captures
it. Which Stripe account is used — test (sandbox) or live — is the server's choice; for
anything but production, use the sandbox keys in the server's `.env`. A cancelled sheet
records nothing; a dropped connection after the card was authorised leaves **Retry card
payment**, which records the same payment.

## Testing

```bash
npm run check-lib    # src/lib matches web/src/lib
npm run typecheck    # tsc --noEmit
npm run lint         # eslint-config-expo
npm test             # jest-expo unit tests
npx expo-doctor      # Expo SDK / dependency checks
```

Unit tests cover the shared lib (the web tests, ported), the money math (expected values
taken from the server's `order_math`), the projection, the event builders, the sync engine
against a scripted server, the approval / discount / refund / tip / open-orders logic, the
request time limit and the server address.

**End to end against a live API** (`src/offline/sync.e2e.test.ts`, skipped unless pointed at
a server): two tills queue an order offline (place, kitchen, add, surcharge, part-pay),
sync, and must agree with the server to the cent; re-pushing is a no-op; the second till
pulls and pays the rest; a late payment on the first till is flagged; a refused placement
holds back its changes.

```bash
VOIDPOS_DATABASE_URL=sqlite:///./e2e.db python scripts/seed_dev.py
VOIDPOS_ADMIN_TOKEN=<24+ chars> VOIDPOS_DATABASE_URL=sqlite:///./e2e.db uvicorn void_pos.main:app --port 8765
# two pairing codes: POST /admin/locations/<location>/devices for each
VOIDPOS_E2E_API=http://127.0.0.1:8765 VOIDPOS_E2E_CODES=<code1>,<code2> npx jest e2e
```

## Builds

- **Android**: `.github/workflows/mobile-android.yml` runs `expo prebuild` and Gradle on a
  GitHub runner for every PR touching the till (and on demand, with an optional default
  server address) and uploads `void-pos-till-android` (`app-release.apk`, arm64 + x86_64).
  It is signed with the generated debug key: for sideloading onto venue tablets, not the
  Play Store. No Expo or Google account is involved.
- **iOS**: not built. It needs a Mac with Xcode (or EAS Build with an Apple developer
  account). `npx expo prebuild --platform ios` then build in Xcode.

## Scope / known gaps

- **iOS build** — blocked on a Mac / Apple developer account (see Builds).
- **Hub mode** — one device relaying between tills when the internet drops: design note in
  [`docs/hub-mode.md`](../docs/hub-mode.md), not built.
- **Kitchen tickets offline** print when the server is back, not at the tap; printing from
  the till itself (and from a hub) needs an ESC/POS builder on the device.
- **Manager approvals offline** — voids, discounts, refunds and taking off sent items need
  the server (it checks the PIN); offline approval needs signed approvals.
- **First sync on a new till** pulls the location's whole event history once (500 a page;
  only the last two days are kept on the device). A "start from now" cursor would avoid it.
- **Not ported**: the receipt view / reprint and "mark sold out" from the till (both online
  features of the web till), and Apple Pay / Google Pay in the Stripe sheet.
- **Navigation** — a lightweight in-app view switch, not a navigation library.
