# Void POS — Web UI

The in-person point-of-sale front end: pair the till, load the menu, build an order (items
with modifier child lines), place it, send it to the kitchen, and take a cash payment, plus
a shift-closeout view. Built with **Next.js (App Router) · TypeScript · Tailwind CSS**,
talking to the FastAPI backend in this repo (`src/void_pos`).

Money is handled as **integer cents** end to end; amounts are only formatted to AUD for
display.

## Prerequisites

- Node 20+ and npm
- The backend installed and runnable (see the repo root `README.md`)

## 1. Run the backend with seed data

From the **repo root**, in your Python venv:

```bash
pip install -e ".[dev]"

# Seed one org / location / till + a small menu into a local sqlite DB.
# Prints a one-time pairing code for the till.
python scripts/seed_dev.py

# Run the API against that SAME database.
VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app
```

On Windows PowerShell, set the env var first:

```powershell
$env:VOIDPOS_DATABASE_URL = "sqlite:///./void_pos_dev.db"
uvicorn void_pos.main:app
```

The API serves on `http://localhost:8000`. The web dev server proxies `/api` to it, so the
browser never calls the API cross-origin.

## 2. Run the web app and pair the till

```bash
cd web
npm ci
npm run dev                       # http://localhost:3000
```

Enter the pairing code the seed script printed. The till keeps its token and counters in
the browser's local storage. Re-run the seed script for a fresh code; each code works once.
If the API isn't on `http://localhost:8000`, set `VOIDPOS_API_URL` in `web/.env.local`
(see `.env.example`).

## Flow

1. **Pair** — the till redeems a pairing code for its bearer token, location, order-number
   range and where its counters resume.
2. **Order** (`/`) — the till loads `GET /locations/{id}/menu` (items, their modifier
   groups, daily windows) and refreshes it every few minutes. Items not on sale now stay on
   the grid, greyed out with the reason ("Unavailable", "11:00–15:00"). Tap an item to pick
   its quantity and modifiers: a pick-one group swaps its choice, other groups take several
   (with a count each, up to the group's maximum), and **Add to order** waits until every
   required group has its choices. A **note for the kitchen** goes on as a free-text line.
   **Place order** posts to `POST /orders`; if the server refuses because the menu changed
   (an item switched off, out of its window), the till says why and reloads the menu.
3. Once placed, the order shows its lines and which ones the kitchen hasn't had. **Send to
   kitchen** prints the order; add more items later (tap dishes, then **Add N items to the
   order**) and Send prints just those. A dine-in order takes a table number, and **Open
   orders** lists every unsettled order to pick a table back up.
4. Payment: the amount defaults to what is owing; type a smaller amount to take part of it,
   or **Split 2/3/4** to take even shares one after another (in whole cents: $10.00 three
   ways is 3.34, 3.33, 3.33). Each share is **Pay cash**, or **Card on terminal** for a card
   taken on the venue's own terminal: the cashier types the terminal's receipt or approval
   number, and it's recorded as an `external` payment. The order turns paid once its
   payments cover the total. **Receipt** shows the tax invoice to send to the receipt
   printer or print from the tablet.
5. **Void order** (before any payment) asks for a reason, then a manager's PIN if the venue
   has managers; if the kitchen has the order, a VOID ticket prints. **Mark sold out**
   switches dishes off and on, with the same PIN. **New order** starts the next ticket.
6. **Closeout** (`/closeout`) — `GET /reports/{location_id}/closeout` for a business date in
   the location's timezone: money taken, sales and GST, what's still owing, order counts,
   payments by provider, revenue by class.

## Customer storefront (`/order`)

A separate, customer-facing app under `/order/<location id>` — no till pairing, no token.
A customer browses the location's live menu (the same `GET` the till uses, served publicly
at `GET /online/locations/{id}`), builds an order with modifiers, gives their name and a
phone or email, chooses pickup or delivery (with an address), optionally redeems loyalty
points, and either pays by card (the same Stripe card form the till uses) or pays at pickup.

The order is placed on the **`web` channel** into the *same* order pipeline as the tills
(`POST /online/locations/{id}/orders`), so it shows up in Open orders and the kitchen like
any other. The storefront mints the order id, so a double-tap or a dropped connection
replays rather than placing twice. A returning customer is remembered in this browser
(`src/lib/online/store.ts`) so their points and details come back; the server still
re-matches them by phone/email and is the source of truth. Pure logic (the API client,
redeem maths, the remembered-customer store) is unit tested under `src/lib/online`.

`NEXT_PUBLIC_DEFAULT_LOCATION` makes bare `/order` redirect to one location's storefront.

## Retries are safe

Every action mints its ids on the till (ULIDs for the order, its lines, the event and the
payment, plus the till's next `seq`) before the request goes out, and keeps them until the
server answers. If the Wi-Fi drops or the kitchen printer is off, tapping again resends the
same ids and the server replays the first result, so nothing is placed, printed or charged
twice. See `src/lib/commands.ts`.

## Scripts

- `npm run dev` — dev server
- `npm run build` / `npm run start` — production build and serve
- `npm run typecheck` — `tsc --noEmit`
- `npm test` — unit tests (vitest) for ids, till counters, retries, the API client, the menu
  rules and orders, and the storefront libs (`src/lib/online`)

## Known gaps

- **Offline**: the till needs the server to place orders. A PWA served over plain HTTP from
  a LAN address can't register a service worker, so working offline needs local HTTPS or the
  React Native app (an open decision in the architecture doc).
- Items already on a placed order can't be changed or removed (void it and ring it up
  again); new items can be added.
