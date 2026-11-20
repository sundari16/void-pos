# Venue end-to-end test plan (V1)

How to prove a Void POS venue install works end to end before a real service: the Docker
stack (PostgreSQL, API, web till, backups), the tablets, the printers and every V1 feature.
Part 1 is automated and can be re-run at any time. Part 2 is the hands-on pass at the venue
with the real hardware and accounts.

**In scope:** the open-core venue install (`docker compose`), the web till and back office,
the customer storefront, and the V1 modules (online ordering, CRM, loyalty, marketing,
request board, aggregator and delivery seams, voice intake, rostering, Xero).
**Out of scope:** `void-pos-cloud` (the hosted commercial layer), the React Native till
(`mobile/`, still a scaffold), the voice telephony stack, and the real Uber Direct / Uber
Eats / DoorDash adapters (commercial; the open core ships mocks).

## Before you start

| Need | Notes |
|---|---|
| Venue machine with Docker | Fixed address on the venue network (DHCP reservation). |
| Kitchen printer (ESC/POS, port 9100) | Plus a receipt printer if the venue uses one. A Tamil font in `./fonts` for non-Latin tickets. |
| Tablets | One per till, on the venue network. A phone for the storefront. |
| `.env` | `POSTGRES_PASSWORD`, `VOIDPOS_ADMIN_TOKEN`, printer hosts. Turn on what you'll test: `VOIDPOS_LOYALTY_ENABLED=true`, `VOIDPOS_AGGREGATOR_WEBHOOK_SECRET=...`. |
| Stripe **test** keys | `sk_test_…` / `pk_test_…` for card-on-screen and storefront card checkout. |
| The real menu | MCH menu and modifier CSVs (see `docs/menu-template.csv`, `docs/modifier-template.csv`). |
| Optional accounts | Xero developer app + demo company, Deputy token, Square token, Twilio/SendGrid — only for the optional checks below. |

## Part 1 — Automated pass

Run it on any machine with Docker. To avoid touching a stack already running there, give
this one its own project name and ports (the compose file is named `void-pos`, so a plain
`docker compose up` would take over an existing install):

```bash
# .env with WEB_PORT=3100 and API_PORT=8100 added
docker compose -p voidpos-e2e up -d --build --wait
set -a; . ./.env; set +a
python scripts/smoke_test.py --api http://localhost:3100/api   # through the web proxy
python scripts/v1_smoke.py  --api http://localhost:3100/api   # the V1 features
(cd web && npm ci && E2E_BASE_URL=http://localhost:3100 E2E_API_URL=http://localhost:8100 \
  E2E_ADMIN_TOKEN="$VOIDPOS_ADMIN_TOKEN" PW_CHANNEL=msedge npx playwright test)
docker compose -p voidpos-e2e down -v                         # when done
```

| Suite | Covers | Result (20 Nov 2026, `main` + this branch) |
|---|---|---|
| `scripts/smoke_test.py` | location, till pairing, menu, order, kitchen send, cash, payment replay, closeout, export access | 12/12, via the web proxy and the API |
| `web/e2e` (Playwright, Edge) | pair; table order + add-ons; remove with manager PIN; discount vs holiday surcharge; split bill cash + tip / card terminal; GST tax invoice; partial refund; void; sold out; closeout; back-office dish → till | 11/11 |
| `scripts/v1_smoke.py` | storefront order, CRM history, loyalty earn, campaign dry run + send, request board (incl. admin-only status), aggregator webhook (secret, ingest, replay, menu sync), mock courier (dispatch, replay, track), voice quote + order, closeout by provider, export access | 39/39, via the web proxy and the API |
| CLI in the container | rostering → clock on/off → timesheet build → approve (manager PIN); `xero-push --dry-run`; menu + modifier imports (re-import changes nothing); nightly backup dump; `print-test` without a printer gives a clear error | all as expected |
| Storefront page | `/order/<location id>` shows the live menu; a required choice (spice level) blocks *Add to order* | as expected |

**Found and fixed during this pass**

- **Docker install dropped the V1 settings.** The `api` service only forwarded the original
  settings, so loyalty, the aggregator webhook, Xero, Deputy and Twilio/SendGrid couldn't be
  switched on at a venue (loyalty showed off, the webhook answered "off"). `docker-compose.yml`
  now passes `.env` to the API; CI asserts a setting from `.env` takes effect.
- **Xero dry-run note** about unsettled orders said a rounding line absorbs a difference
  even when there is none; it now says those orders aren't in the invoice.

## Part 2 — At the venue

Tick each row; note anything odd in the last column. Rows marked *(optional)* need an
account that isn't required for the pilot.

### A. Install and setup

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| A1 | `docker compose up -d --build`, `docker compose ps` | db, api, web healthy | | |
| A2 | `void-pos create-location`, `venue-details` (ABN, address, phone) | receipts become tax invoices; couriers have a pickup address | | |
| A3 | `add-staff` for each manager (PIN typed, not echoed) | listed by `list-staff` | | |
| A4 | `import-menu` / `import-modifiers` with the real CSVs, `--dry-run` first | dry run lists changes; real run matches; re-run changes nothing | | |
| A5 | `add-device` per tablet; pair each at `http://<machine>:3000` | each tablet shows the till | | |

### B. Printers

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| B1 | `void-pos print-test` | ticket prints, Tamil line legible | | |
| B2 | Send an order to the kitchen | kitchen ticket with table number | | |
| B3 | Add a dish to a sent order, send again | ADDED ticket with just that dish | | |
| B4 | Void a sent order (manager PIN) | VOID ticket | | |
| B5 | Print a receipt (or *Print from this device*) | GST tax invoice with ABN | | |
| B6 | Turn the kitchen printer off, send an order | till says the printer isn't answering; order stays open; resend prints once | | |

### C. Service on the real tablets

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| C1 | Dine-in order with a table, then add dessert from *Open orders* | both tickets print; one order | | |
| C2 | Split a bill: half cash with a tip, half *Card on terminal* (type the approval number) | closeout shows cash and card separately, tip counted once | | |
| C3 | Discount (manager PIN) and the public-holiday surcharge | totals and GST correct on the receipt | | |
| C4 | Partial refund of a cash payment (manager PIN) | closeout nets the refund and its GST | | |
| C5 | Mark a dish sold out and back on (manager PIN) | greys out on every till within a few minutes | | |
| C6 | Restart the API mid-order (`docker compose restart api`) | till says it can't reach the server; retrying doesn't double-charge | | |

### D. Card payments (Stripe test mode)

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| D1 | Put `sk_test_`/`pk_test_` keys in `.env`, `docker compose up -d api` | till offers card on screen | | |
| D2 | Pay with test card `4242 4242 4242 4242` | captured once; payment shows the Stripe reference | | |
| D3 | Refund part of it (manager PIN) | refund appears in the Stripe test dashboard | | |

### E. Online storefront, CRM and loyalty

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| E1 | On a phone, open `http://<machine>:3000/order/<location id>` | live menu; required choices enforced | | |
| E2 | Check out a pickup order (name, phone, opt in to marketing) | order reaches the tills and the kitchen | | |
| E3 | Pay it by card on the storefront (needs D1) | paid online; closeout shows it | | |
| E4 | With loyalty on, pay a web order; place another and redeem points | points earned, then redeemed; balance never stored, always the ledger sum | | |

### F. Channels and integrations

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| F1 | Send a mock aggregator order (`void-pos aggregator-order --file order.json`, or `scripts/v1_smoke.py`) | kitchen ticket prints; closeout shows `aggregator` | | |
| F2 | Own-channel delivery order → `delivery-dispatch` (mock courier) → `delivery-status` | courier requested and tracked | | |
| F3 | `void-pos send-campaign --channel sms --dry-run` | only opted-in customers selected | | |
| F4 | *(optional)* real SMS/email via Twilio/SendGrid | message received | | |
| F5 | Staff clock on/off on the day; `timesheet-build`, `timesheet-approve` (manager PIN) | worked/paid hours match the clock | | |
| F6 | *(optional)* `deputy-connect` / `deputy-sync` with the venue's Deputy | rosters in, approved timesheets out | | |
| F7 | `xero-push --dry-run` for the day | invoice reconciles: sub-total + GST = money taken by provider | | |
| F8 | *(optional)* `xero-connect` to the Xero demo company, then `xero-push` twice | one invoice, paid into the mapped accounts; the second push updates it | | |
| F9 | *(optional)* `import-square` with the venue's Square token, `--dry-run` first | items, prices, GST and modifier sets match Square | | |

### G. Close of day, backups, export

| # | Do | Expect | Pass | Notes |
|---|---|---|---|---|
| G1 | Closeout for the day | totals by provider match the cash count and the terminal batch; GST matches the receipts | | |
| G2 | `./backups` has a dump; copy it off the machine | file present and readable elsewhere | | |
| G3 | Restore that dump into a **test** database (not the live one) | data matches | | |
| G4 | `GET /export/<location id>` with the admin token | full JSON export; CSVs open in a spreadsheet | | |

## Notes from the automated pass

- Customers belong to the organisation, so a customer who orders at one location is the
  same customer at the others (by design).
- Rostering CLI times are local venue time, `YYYY-MM-DD HH:MM`.
- Delivery dispatch needs the venue address (`venue-details`) set first.
