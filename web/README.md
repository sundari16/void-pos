# Void POS — Web UI

The in-person point-of-sale front end: load the menu, build an order (items with
modifier child lines), place it, send it to the kitchen, and take a cash payment —
plus a shift-closeout view. Built with **Next.js (App Router) · TypeScript · Tailwind CSS**,
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

# Seed one org / location / device + a small menu into a local sqlite DB.
# Prints the LOCATION_ID / DEVICE_ID to paste into web/.env.local.
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

## 2. Configure and run the web app

```bash
cd web
cp .env.example .env.local        # then paste the IDs printed by seed_dev.py
npm install
npm run dev                       # http://localhost:3000
```

`.env.local` values:

| Variable | Meaning |
| --- | --- |
| `NEXT_PUBLIC_API_BASE` | Backend base URL (default `http://localhost:8000`) |
| `NEXT_PUBLIC_LOCATION_ID` | Location this till rings orders against |
| `NEXT_PUBLIC_DEVICE_ID` | This POS device (owns a private order-number range) |
| `NEXT_PUBLIC_ORDER_NUMBER_START` | First order number to mint (inside the device's range) |

If the location/device aren't set, the app shows a configuration notice with these steps.

## Flow

1. **Order** (`/`) — tap a menu item to open a customize sheet (quantity + modifiers),
   add it to the ticket. The running total and item count update live. **Place order**
   posts to `POST /orders`.
2. Once placed, **Send to kitchen** (`POST /orders/{id}/send-to-kitchen`) and
   **Pay cash** (`POST /orders/{id}/pay`) act on the order; the state badge follows
   `open → sent → paid`. **New order** starts the next ticket.
3. **Closeout** (`/closeout`) — reads `GET /reports/{location_id}/closeout` for a chosen
   business date: gross sales, order counts by state, payments by provider, revenue by class.

## Scripts

- `npm run dev` — dev server
- `npm run build` / `npm run start` — production build and serve
- `npm run typecheck` — `tsc --noEmit`

## Notes & known gaps

- **Order numbering** is minted client-side per device (localStorage), seeded from
  `NEXT_PUBLIC_ORDER_NUMBER_START`, and advances only after a successful place. This
  matches the backend contract (the client supplies `order_number`); a server-side
  allocator is a later refinement.
- **Modifiers** are client-side presets (spice / extras / prep notes) applied as modifier
  child lines, because this slice of the API exposes no endpoint to read which modifier
  groups apply to an item. When the backend adds a modifier-read endpoint, swap
  `src/lib/modifiers.ts` for a fetch. The order shape (`ref` / `parent_ref` tree, price
  snapshots in cents) already matches real catalog modifiers.
- Editing a ticket is disabled once the order is placed (this slice has no amend/void UI).
