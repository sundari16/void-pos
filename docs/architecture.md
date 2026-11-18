# Void POS architecture

How the open core fits together. Product decisions live in the architecture decisions doc
linked from the README; this page is the engineering view of what is built.

## Deployment: one machine on the venue's network

Until Void POS is commercial it runs self-hosted only (no cloud). One always-on machine at
the venue runs everything with `docker compose`; see [self-host.md](self-host.md).

| Service | What it is | Talks to |
|---|---|---|
| `web` | Next.js till, standalone server | proxies `/api/*` to `api` |
| `api` | FastAPI on uvicorn; migrates on start | `db`; the kitchen printer over TCP 9100 |
| `db` | PostgreSQL 17 | |
| `backup` | `pg_dump` at start and nightly into `./backups` | `db` |

Tablets open `http://<venue machine>:3000` and only ever talk to that origin. Because the
API is on the venue network, an internet outage doesn't stop service, and the API can open
a socket straight to the kitchen printer.

## Code map

| Path | Responsibility |
|---|---|
| `src/void_pos/models/` | SQLAlchemy models: the event log plus projected aggregates |
| `src/void_pos/events/` | `ingest_event` / `apply_events`: validate, append, project; payload schemas per event type |
| `src/void_pos/commands.py` | The online write path: one till command becomes one event, replay-safe |
| `src/void_pos/orders/`, `payments/` | Place order, send to kitchen, pay (provider seam: cash, external; Stripe Connect is commercial) |
| `src/void_pos/sync/` | Device push and cursor pull, scoped to a location |
| `src/void_pos/devices/` | Locations, tills, pairing codes, device tokens |
| `src/void_pos/menu/`, `reporting/`, `export/`, `printing/` | Catalog, closeouts, full export, ESC/POS tickets |
| `src/void_pos/api/` | HTTP layer: auth, schemas, routes, error mapping. Routes stay thin |
| `src/void_pos/cli.py` | `void-pos` venue setup commands |
| `web/src/lib/` | Till state, ULIDs, retry-safe commands, typed API client (unit tested) |

## The write path

The event log is the source of truth; `pos_order`, `order_line`, `payment` and
`loyalty_ledger` are projections of it. Two paths write events, and both go through
`ingest_event`:

1. **Online commands** from a paired till: `POST /orders`, `/orders/{id}/send-to-kitchen`,
   `/orders/{id}/pay`.
2. **Sync**: a till pushes a batch of events it recorded (`POST /sync/push`) and pulls the
   rest of its location's events by cursor (`GET /sync/pull`). The cursor is a
   per-location `sync_seq` handed out after commit, under the location's counter lock, so
   a late-committing write always lands after the cursor and is never skipped.

**The till mints every id** (data-model rule 1): the event id and its per-till `seq`, plus
the order, line and payment ids, all ULIDs. A retried request carries the same ids, so the
server replays the first result: no second order, reprint or charge. Commands also check
everything before side effects: paying locks the order row and checks its state and
total before the provider is charged (two tills can't both settle it), and the kitchen
ticket prints just before its event is written, so a dead printer records nothing.

`ingest_event` refuses an event, with a reason code and without touching the log, when:

| Code | Meaning |
|---|---|
| `unknown_device` | the device isn't registered |
| `location_mismatch` | the event's location isn't the device's |
| `seq_conflict` | this device already used that seq (a command answers 409 with `last_seq`) |
| `invalid_payload` | the payload fails its event type's schema (strict on money and counts), e.g. a modifier pointing nowhere |
| `order_number_out_of_range` | the order number is outside the till's range |
| `order_number_used` | this till already used the number (a command answers 409 with `next_order_number`) |
| `order_exists` | an order with this id already exists |
| `foreign_order` | the event touches another location's order |
| `unknown_customer` | a loyalty entry names a customer outside this org |

In a sync batch a refused event is acked `rejected` and the rest still apply. A reference
to a menu item, modifier or customer that doesn't exist at the location (deleted since, or
sold from a stale menu) is dropped from the projection rather than failing the event: the
line's snapshot already records what was sold. A till's `last_seq` only ever moves forward
(one conditional `UPDATE`). Events apply
in canonical `(occurred_at, event_id)` order, and the order state machine settles
conflicting transitions deterministically.

## Who may do what

Every request except `/health*` and `/devices/pair` carries `Authorization: Bearer <token>`.

- **Venue admin**: `VOIDPOS_ADMIN_TOKEN` (24+ characters, compared in constant time).
  Creates locations and tills, issues pairing codes, revokes tills, edits the menu, exports.
- **Till**: registered by the admin with a private order-number range and a one-time
  pairing code (8 characters, 15 minutes). Redeeming it returns a bearer token, the till's
  location, and where its seq and order numbering resume. Only SHA-256 hashes of codes and
  tokens are stored. A till's device and location always come from its token, never from
  the request, so it only ever sees its own location's menu, orders, reports and events.

| | Till | Admin |
|---|---|---|
| Orders, kitchen, pay, sync | yes | no (`till_only`) |
| Read menu, order, closeout | own location | any |
| Edit menu, export, `/admin/*` | no (`admin_only`) | yes |

## Errors

Every failure is JSON: `{"detail": "...", "code": "...", ...}`. `detail` is for people,
`code` is stable for clients to branch on, and extra fields help recovery (`last_seq`,
`total_cents`, `order_state`). Validation errors are 422 `invalid_request` with a readable
`detail` and the field `errors`. A uniqueness race the checks missed is 409 `conflict`, and
anything unexpected is a logged 500 `internal_error`.

| Status | Codes |
|---|---|
| 401 | `missing_token`, `invalid_token`, `invalid_pairing_code`, `pairing_code_expired`, `device_revoked` |
| 402 | `payment_failed` |
| 403 | `till_only`, `admin_only`, `admin_disabled`, `wrong_location`, `foreign_event` |
| 404 | `not_found`, `unknown_entity` |
| 409 | `seq_conflict`, `order_number_used`, `event_id_reused`, `order_exists`, `foreign_order`, `order_closed`, `order_voided`, `payment_exists`, `range_overlap`, `device_revoked` |
| 422 | `invalid_request`, `amount_mismatch`, `order_number_out_of_range`, `invalid_timezone`, `invalid_range`, `invalid_cursor`, `invalid_selection_limits`, `location_mismatch` |
| 503 | `printer_unavailable`, `database_unavailable` |

The web till keeps a command's body until the server answers: a network error or 5xx keeps
the ids for a safe retry (and freezes the ticket, so the retry is the same order), a 4xx
drops them, a `seq_conflict` or `order_number_used` resyncs the counter and resends once,
and a 401 sends the till back to pairing. Two tabs of one till share counters through
local storage.

## Time and money

Money is integer cents (AUD by default). Timestamps are stored in UTC. Business dates follow
the location's timezone, so an order rung up at 8am in Brisbane is on that day's closeout,
not the previous UTC day's.

## Delivery

- `constraints.txt` pins every Python dependency; CI and the API image install with it.
  `web/package-lock.json` does the same for the web till.
- CI (`.github/workflows/pr-checks.yml`): ruff, mypy and pytest on SQLite **and**
  PostgreSQL; `alembic upgrade`, `check` (models match migrations), `downgrade` and
  `upgrade` again; web typecheck, unit tests and build; then the full Docker stack with the
  smoke test through the web proxy and the API, a backup check and the setup CLI.
- `.githooks/pre-push` runs the fast subset locally.

## Not built yet

- **Offline tills.** The till needs the venue server. Working offline means a per-device
  store with an outbox draining through `/sync/push` (the server side exists), and a PWA
  over plain HTTP can't register a service worker: that needs local HTTPS or the React
  Native app.
- **Hosting.** GCP is deferred until the project is commercial. Moving there means a
  print agent on a venue device (the cloud can't reach a LAN printer) and per-org limits
  on top of the location scoping that already exists.
- A menu read endpoint with modifier groups (the till uses preset modifiers), split
  payments, voids from the till, and rate limiting on pairing.
