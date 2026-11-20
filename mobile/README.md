# Void POS — Mobile till

The in-person point-of-sale as a **React Native (Expo)** app for iOS and Android: pair the
till, load the menu, build an order (items with modifier child lines), place it, send it to
the kitchen, take a **cash** or **card-on-terminal** payment, and read the **shift closeout**.
It talks to the same FastAPI backend as the web till (`src/void_pos`) and mirrors its flows
and API contracts exactly.

Money is handled as **integer cents** end to end; amounts are only formatted to AUD for
display.

> **Status:** scaffold. The app **typechecks** (`tsc --noEmit`), **lints**, and its unit tests
> pass. It has not been built for a device here — an iOS build needs a Mac, and no Android
> build has been produced. See [Scope](#scope--known-gaps).

## What's shared with the web till

The order/menu/payment rules live in `src/lib/`, ported from `web/src/lib/` so the two tills
can't drift:

- `types.ts` — TypeScript mirrors of the backend schemas (`src/void_pos/api/schemas.py`).
- `ulid.ts` — device-minted ULIDs (idempotent writes; a retry resends the same ids).
- `menu.ts` — availability windows, modifier selection rules.
- `order.ts` — the draft order and its flattening to backend `LineIn`s (item line, then its
  modifier child lines via `parent_line_id`).
- `placed.ts` — a placed order grouped for display; applying server answers.
- `payments.ts` — cash / card-on-terminal amounts and splits.
- `commands.ts` — the safe-retry wrapper (`runCommand`).
- `api.ts` — the typed API client.

Mobile-specific pieces: `config.ts` (reads `EXPO_PUBLIC_API_BASE`), `money.ts` (a hand-rolled
AUD formatter — avoids Hermes `Intl` differences across platforms), `till.ts` + `storage.ts`
(the till's token and counters in the device's AsyncStorage), and the screens under
`src/screens/`.

## Prerequisites

- Node 20+ and npm
- The Void POS backend installed and runnable (see the repo root `README.md`)
- To run on a device/simulator: [Expo Go](https://expo.dev/go) on a phone, or an iOS
  simulator (Mac) / Android emulator

## 1. Run the backend with seed data

From the **repo root**, in your Python venv (same as the web till — see `web/README.md`):

```bash
pip install -e ".[dev]"

# Seed one org / location / till + a small menu into a local sqlite DB.
# Prints a one-time pairing code for the till.
python scripts/seed_dev.py

# Run the API against that SAME database, bound so a device can reach it.
VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app --host 0.0.0.0
```

On Windows PowerShell:

```powershell
$env:VOIDPOS_DATABASE_URL = "sqlite:///./void_pos_dev.db"
uvicorn void_pos.main:app --host 0.0.0.0
```

The API serves on port `8000`. Unlike the web till (which proxies `/api` on its own origin),
the mobile till calls the API **directly**, so for anything but the iOS simulator the API must
allow the device's origin — start it with `VOIDPOS_CORS_ORIGINS` covering the URL you point the
app at (see step 2).

## 2. Point the app at the backend

Copy `.env.example` to `.env` and set `EXPO_PUBLIC_API_BASE` to a URL the device can reach:

| Running on | `EXPO_PUBLIC_API_BASE` |
| --- | --- |
| iOS simulator | `http://localhost:8000` (shares the Mac's network) |
| Android emulator | `http://10.0.2.2:8000` (the emulator's alias for the host) |
| Real device (Expo Go) | `http://<your-computer-LAN-ip>:8000`, e.g. `http://192.168.1.20:8000` |

Without the variable the app defaults to `http://localhost:8000`. Expo inlines
`EXPO_PUBLIC_*` at build time, so restart the dev server after changing `.env`.

## 3. Run the app and pair the till

```bash
cd mobile
npm install
npm start           # Expo dev server; press i (iOS), a (Android), or scan the QR in Expo Go
```

Enter the pairing code the seed script printed. The till keeps its token and counters in the
device's storage, so it stays paired across restarts. Re-run the seed script for a fresh code;
each code works once. **Unpair** (top-right) clears it.

## Flow

1. **Pair** — redeem a pairing code for the till's bearer token, location, order-number range
   and where its counters resume.
2. **Order** — the app loads `GET /locations/{id}/menu` (items grouped by category, their
   modifier groups and daily windows) and refreshes it periodically. Items not on sale now
   stay on the grid, greyed out with the reason. Tap an item to choose quantity and modifiers
   (a pick-one group swaps its choice; other groups take several up to their maximum) and add a
   note for the kitchen. **Add to order** waits until every required group has its choices.
3. **Ticket** — the cart bar opens the order ticket: item lines with their modifier child lines
   and a running AUD total. **Place order** posts to `POST /orders`; if the menu changed under
   the till, it says why and reloads.
4. Once placed, the ticket shows the lines and which the kitchen hasn't had. **Send to kitchen**
   prints the order (`POST /orders/{id}/send-to-kitchen`); add more items first and Send prints
   just those. **Pay** defaults to the balance: **Pay cash**, or **Card on terminal** (record
   the terminal's receipt/approval number as an `external` payment). The order turns paid once
   payments cover the total.
5. **New order** starts the next ticket. **Closeout** reads
   `GET /reports/{location_id}/closeout` for today in the location's timezone: money taken,
   sales and GST, what's owing, order counts, payments by provider, revenue by class.

## Retries are safe

Every action mints its ids on the till (ULIDs for the order, its lines, the event and the
payment, plus the till's next `seq`) before the request goes out, and holds them until the
server answers. If the Wi-Fi drops or the kitchen printer is off, tapping again resends the
same ids and the server replays the first result, so nothing is placed, printed or charged
twice. A `seq_conflict` resyncs the counter and resends once. See `src/lib/commands.ts`.

## Scripts

- `npm start` — Expo dev server (`npm run android` / `npm run ios` / `npm run web` target one)
- `npm run typecheck` — `tsc --noEmit`
- `npm run lint` — ESLint (`eslint-config-expo`)
- `npm test` — unit tests (jest-expo) for ids, money, menu rules, draft orders, placed-order
  helpers, safe-retry and till counters

## Scope / known gaps

This scaffold covers the core till flow: **menu → order → place → send to kitchen → cash /
card-on-terminal pay → closeout**, against the real API. Deliberately left for later (the web
till already has some of these; the shared `src/lib/` supports them):

- **iOS/Android builds** — not produced here (iOS needs a Mac). `npm start` runs it in Expo Go
  or a simulator/emulator.
- **Stripe card-on-screen, voids, refunds, discounts/surcharges, manager-PIN approvals, and the
  open-orders picker** — out of this scaffold; cash and card-on-terminal are wired.
- **Offline** — the app needs the server to place orders; the device sync path
  (`/sync/push` · `/sync/pull`) isn't wired in yet.
- **Navigation** — a lightweight in-app view switch, not a navigation library, for a handful of
  screens.
