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

The API serves on `http://localhost:8000`. CORS already allows `http://localhost:3000`
(override with `VOIDPOS_CORS_ORIGINS`).

## 2. Run the web app and pair the till

```bash
cd web
npm ci
npm run dev                       # http://localhost:3000
```

Enter the pairing code the seed script printed. The till keeps its token and counters in
the browser's local storage. Re-run the seed script for a fresh code; each code works once.
To point the till at an API somewhere other than port 8000 on the same host, set
`NEXT_PUBLIC_API_BASE` in `web/.env.local` (see `.env.example`).

## Flow

1. **Pair** — the till redeems a pairing code for its bearer token, location, order-number
   range and where its counters resume.
2. **Order** (`/`) — tap a menu item to open a customize sheet (quantity + modifiers), add
   it to the ticket. **Place order** posts to `POST /orders`.
3. Once placed, **Send to kitchen** and **Pay cash** act on the order; the state badge
   follows `open → sent → paid`. **New order** starts the next ticket.
4. **Closeout** (`/closeout`) — `GET /reports/{location_id}/closeout` for a business date in
   the location's timezone: gross sales, order counts, payments by provider, revenue by class.

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
- `npm test` — unit tests (vitest) for ids, till counters, retries, the API client and orders

## Known gaps

- **Offline**: the till needs the server to place orders. A PWA served over plain HTTP from
  a LAN address can't register a service worker, so working offline needs local HTTPS or the
  React Native app (an open decision in the architecture doc).
- **Modifiers** are client-side presets (spice / extras / prep notes) applied as modifier
  child lines, because the API exposes no endpoint yet to read which modifier groups apply
  to an item. The order shape already matches real catalog modifiers.
- Editing a ticket is disabled once the order is placed (no amend/void UI yet).
