# Void POS architecture

How the open core fits together. Product decisions live in the architecture decisions doc
linked from the README; this page is the engineering view of what is built.

## Deployment: one machine on the venue's network

**Target topology (decided 18 Nov 2026).** The cloud is the system of record. Tills are the
React Native app with an on-device store and an outbox, printing straight to the venue's
printer, so they keep taking orders when the internet drops; meanwhile one of the venue's
own devices switches to hub mode and relays events between tills over the local network.
Online ordering, delivery platforms and payment webhooks live in the cloud. Every venue is
one fleet: no per-venue server to patch, back up or replace.

**Today, and for the Madras Curry House pilot,** Void POS runs self-hosted (no cloud until
the project is commercial): one always-on machine at the venue runs everything with
`docker compose`; see [self-host.md](self-host.md). This stays as the open core's
self-host option, and it is the same server code the cloud will run.

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
| `src/void_pos/orders/`, `payments/` | Place order (with the menu rules in `orders/rules.py`), send to kitchen, void, pay in full or in parts. Hybrid (decided 18 Nov 2026): cash and card-on-terminal (`external` + the terminal's reference) now; Stripe Connect as the commercial default; one terminal integration later if venues need it |
| `src/void_pos/totals.py`, `balance.py` | Order arithmetic shared by validation, payments, reports and the till: a modifier's quantity is per unit of its item; the balance is the total less captured payments |
| `src/void_pos/sync/` | Device push and cursor pull, scoped to a location |
| `src/void_pos/devices/` | Locations, tills, pairing codes, device tokens |
| `src/void_pos/menu/` | Catalog: items, modifier groups (min/max choices) and modifiers, daily availability windows (`availability.py`), the till's whole-menu read, spreadsheet imports for items and modifiers; every admin edit recorded in a numbered change history, latest change revertible (`history.py`) |
| `src/void_pos/reporting/`, `export/`, `printing/` | Closeouts, full export (including menu history), ESC/POS tickets |
| `src/void_pos/board/` | The public feature request board: submit, list with votes, upvote (idempotent), set status |
| `src/void_pos/api/` | HTTP layer: auth, schemas, routes, error mapping. Routes stay thin |
| `src/void_pos/cli.py` | `void-pos` venue setup commands |
| `web/src/lib/` | Till state, ULIDs, retry-safe commands, typed API client (unit tested) |

## The write path

The event log is the source of truth; `pos_order`, `order_line`, `payment` and
`loyalty_ledger` are projections of it. Two paths write events, and both go through
`ingest_event`:

1. **Online commands** from a paired till: `POST /orders`, `/orders/{id}/lines` (add
   items), `/orders/{id}/send-to-kitchen`, `/orders/{id}/void`, `/orders/{id}/pay`.
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
| `unknown_order` | items added to an order the server hasn't got yet (send its `order.placed` first, then resend) |
| `line_exists` | an added line's id is already used |
| `order_too_long` | the order would pass 1,000 lines |
| `manager_approval_required` | a void at a location with managers that doesn't name one |

In a sync batch a refused event is acked `rejected` and the rest still apply. A reference
to a menu item, modifier or customer that doesn't exist at the location (deleted since, or
sold from a stale menu) is dropped from the projection rather than failing the event: the
line's snapshot already records what was sold. A till's `last_seq` only ever moves forward
(one conditional `UPDATE`). Events apply
in canonical `(occurred_at, event_id)` order, and the order state machine settles
conflicting transitions deterministically.

### Orders, modifiers and the menu

An order is item lines with modifier child lines (data-model rule 2). A modifier's
quantity is **per unit of its item**: two naan, each with one extra butter, is an item line
of quantity 2 and a modifier line of quantity 1, and the butter is charged twice. Totals,
payments, the revenue-by-class report and the till all use the one rule (`totals.py`); a
modifier without its own category counts in its item's class.

Placing an order **online** also checks it against the menu as it is now
(`orders/rules.py`): each item must be on sale (switched on and inside its daily window, in
the location's local time), each catalog modifier's group must apply to its item and be on
sale, and each group gets between its `min_select` and `max_select` choices, counting
quantities. A group outside its window has no minimum. Lines with no menu reference (an
open item, a kitchen note) pass. An order **synced** from a till that was offline is not
checked: it already happened at the counter, so it is recorded as rung up.

A daily window runs from `available_from` (inclusive) to `available_to` (exclusive); one
that ends before it starts runs past midnight, and an open end is unbounded. The till reads
everything in one call, `GET /locations/{id}/menu`: live items with their group ids, every
group with its modifiers, and what is on sale now.

### Tables, adding items and the kitchen

A dine-in order can carry a `table_label`. `GET /locations/{id}/orders` lists the
location's unsettled orders (open or sent, any date) with totals, GST, what's owing and
how many lines the kitchen hasn't had, so a till can pick a table back up; every order
response carries the lines and payments.

`POST /orders/{id}/lines` records `order.lines_added`: new item lines with their
modifiers, checked against the menu like a new order. A paid order is closed (start a new
one); a part-paid one takes more and its balance grows. Each line remembers when the
kitchen got it, projected from `order.sent`, which lists the lines it sent (`line_ids`;
an older till's send without them covers every line not yet sent). So **Send to kitchen**
prints the whole order the first time, then an ADDED ticket with only the new items, or a
REPRINT when there's nothing new.

### Voids and payments

`POST /orders/{id}/void` needs a reason. It is refused once any payment is captured
(refund first) or if the order is already voided. If the kitchen has the order, a VOID
ticket prints first, so a printer that's down records nothing and the till retries.
Once the location has an active manager, a void needs a manager's PIN, and the event
records who approved it.

An order can be paid in parts. Each payment takes some or all of the balance (default: all
of it), never more, and the order turns paid once captured payments cover the total. The
projection applies the same rule to synced payments, so a split bill settles the same way
online or offline. Order and payment responses carry `balance_cents`.

A payment can carry a **tip** (`tip_cents`) on top of what it pays toward the order; tips
are reported apart from sales. **Card payments through Stripe** (sandbox only; the server
refuses live keys) are taken on the till's screen with Stripe's Payment Element: the till
asks for a PaymentIntent (`POST /orders/{id}/stripe-intent`), the card goes from the
browser straight to Stripe, and `POST /orders/{id}/pay` with method `stripe` records it
only after Stripe confirms that intent succeeded for this order, payment and amount.

### Changing a placed order, and giving money back

- **Taking items off** (`POST /orders/{id}/remove-lines`, `order.lines_removed`): an
  item and its modifiers leave every total, ticket and receipt but stay on record. One the
  kitchen has needs a manager's PIN and prints a CANCEL ticket first. A quantity change is
  take off and add again.
- **Discounts and surcharges** (`POST /orders/{id}/adjustments`, `order.adjusted`; and
  `.../remove`): a percentage or an amount on the whole order. Discounts (a manager's
  PIN) come off the items' subtotal, never below zero; surcharges (the public-holiday
  rate the venue sets) go on the discounted subtotal. GST follows each rate's share of
  the final total.
- **Refunds** (`POST /orders/{id}/refunds`, `order.refunded`): money back against one
  payment of a paid order, through its own provider (cash, the card terminal with its
  refund reference, or Stripe), with a manager's PIN; refunded in full, the order is
  `refunded`.

None of these can take an order below what's already been paid (`below_paid`), and
events from sync follow the same rules, approval included.

### GST and receipts

Menu prices include GST. A line at rate *r* carries *r* / (1 + *r*) of its price as GST;
amounts are summed per rate and rounded once per rate, per order (`totals.gst_cents`), so
each receipt and the closeout agree. The closeout reports paid sales, the GST in them,
sales ex GST, discounts and surcharges, tips by provider, refunds by provider with their
share of GST, net sales and net GST after refunds, and what's still owing on open orders.

`GET /orders/{id}/receipt` builds the customer receipt (`receipts/`). With an ABN on the
location it is an Australian tax invoice: the seller's name and ABN, the date, each item
with quantity and amount, the GST included and the words "Tax invoice"; without one it is a
plain receipt. `POST /orders/{id}/print-receipt` prints it on the receipt printer, if one
is configured; otherwise the till shows it to print from the tablet. Printing a receipt
records nothing.

## The request board

A public feature request board, the visibility the incumbents don't give: operators ask for
a feature, upvote the ones they want, and see where each one stands — its status and its
dates — instead of a request vanishing with no trace. It is plain reference data, not part
of the event-sourced order pipeline, so it is stored directly (like staff or the CRM) in two
tables: `feature_request` (title, body, author, status, created/updated dates) and
`feature_request_vote` (one row per `(request, voter)`).

The one data-model rule it keeps is loyalty's: a count is a `SUM` of a ledger, never a
stored field. The vote count is always `COUNT` of the vote rows, and the unique
`(request, voter)` makes an **upvote idempotent** — pressing it again records nothing and
changes no count. The voter is the caller's own identity — a till's device id, or `admin`
for the venue admin — taken from the token, never the body, so one till counts once.

| Endpoint | Who | What |
|---|---|---|
| `POST /board/requests` | any till or admin | submit a request; it starts `submitted` with no votes |
| `GET /board/requests?sort=top\|new` | any till or admin | the board: most-voted first (`top`, default) or newest (`new`), each with its votes, status, dates and whether the caller has upvoted |
| `POST /board/requests/{id}/upvote` | any till or admin | upvote; idempotent |
| `POST /board/requests/{id}/status` | admin | move it to `submitted` / `under_review` / `planned` / `shipped` |

A request's status is one of `submitted`, `under_review`, `planned`, `shipped`; its
`updated_at` moves when the status changes. The back office has a **Board** screen
(`/admin/board`) to submit, upvote and (as admin) move status.

## Who may do what

Every request except `/health*` and `/devices/pair` carries `Authorization: Bearer <token>`.
Pairing counts wrong codes: 10 from one client or 50 overall in 10 minutes, and it answers
429 `too_many_attempts` with `Retry-After` until the oldest ages out (`api/ratelimit.py`).

**Staff and manager approval.** Staff have a role (staff or manager) and a 4-8 digit PIN,
unique at the location and stored as a salted scrypt hash. Once a location has an active
manager, voiding an order or switching a dish off from a till needs a manager's PIN in the
request; the void event or the menu change records who approved it. Wrong PINs are counted
per till (5 in 5 minutes, then 429). A location with no manager needs no PIN, so add one
before going live. The rule holds on every path: an `order.voided` event, online or from
sync, must name an active manager at the location by id and name, or it is rejected
(`manager_approval_required`). A till can't check a PIN offline yet; the React Native till
will need signed approvals for offline voids.

- **Venue admin**: `VOIDPOS_ADMIN_TOKEN` (24+ characters, compared in constant time).
  Creates locations and tills, issues pairing codes, revokes tills, edits the menu, exports.
- **Till**: registered by the admin with a private order-number range and a one-time
  pairing code (8 characters, 15 minutes). Redeeming it returns a bearer token, the till's
  location, and where its seq and order numbering resume. Only SHA-256 hashes of codes and
  tokens are stored. A till's device and location always come from its token, never from
  the request, so it only ever sees its own location's menu, orders, reports and events.

| | Till | Admin |
|---|---|---|
| Orders, add items, kitchen, void, pay, sync | yes | no (`till_only`) |
| Read menu, orders, receipts, closeout; print a receipt | own location | any |
| Switch a dish off or on | with a manager's PIN | yes |
| Edit menu (one item or a bulk edit, all or nothing), export, staff, venue details, `/admin/*` | no (`admin_only`) | yes |
| Request board: submit, upvote | yes | yes |
| Request board: set a request's status | no (`admin_only`) | yes |

## Errors

Every failure is JSON: `{"detail": "...", "code": "...", ...}`. `detail` is for people,
`code` is stable for clients to branch on, and extra fields help recovery (`last_seq`,
`balance_cents`, `order_state`). Validation errors are 422 `invalid_request` with a readable
`detail` and the field `errors`. A uniqueness race the checks missed is 409 `conflict`, and
anything unexpected is a logged 500 `internal_error`.

| Status | Codes |
|---|---|
| 401 | `missing_token`, `invalid_token`, `invalid_pairing_code`, `pairing_code_expired`, `device_revoked` |
| 402 | `payment_failed` |
| 403 | `till_only`, `admin_only`, `admin_disabled`, `wrong_location`, `foreign_event`, `manager_pin_required`, `wrong_pin` |
| 404 | `not_found`, `unknown_entity` |
| 409 | `seq_conflict`, `order_number_used`, `event_id_reused`, `order_exists`, `foreign_order`, `order_closed`, `order_voided`, `order_paid`, `payment_exists`, `range_overlap`, `device_revoked`, `item_unavailable`, `modifier_unavailable`, `changed_since`, `already_reverted`, `in_use`, `group_missing`, `line_exists`, `pin_taken` |
| 422 | `invalid_request`, `amount_exceeds_balance`, `invalid_amount`, `modifier_not_allowed`, `modifier_selection`, `order_number_out_of_range`, `invalid_timezone`, `invalid_range`, `invalid_cursor`, `invalid_selection_limits`, `invalid_time_window`, `duplicate_item`, `location_mismatch`, `invalid_pin`, `invalid_role`, `invalid_abn`, `order_too_long`, `invalid_status` |
| 429 | `too_many_attempts` |
| 503 | `printer_unavailable`, `no_receipt_printer`, `database_unavailable` |

The web till keeps a command's body until the server answers: a network error or 5xx keeps
the ids for a safe retry (and freezes the ticket, so the retry is the same order), a 4xx
drops them, a `seq_conflict` or `order_number_used` resyncs the counter and resends once,
a 401 sends the till back to pairing, and a refusal because the menu changed reloads the
menu. Two tabs of one till share counters through local storage.

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

- **Offline tills.** The web till needs the venue server. The target is the React Native
  till: an on-device SQLite store with an outbox draining through `/sync/push` (the server
  side exists), direct printing, and hub-mode relay between tills when the internet is down.
- **Hosting.** GCP is deferred until the project is commercial. Moving there means a
  print agent on a venue device (the cloud can't reach a LAN printer) and per-org limits
  on top of the location scoping that already exists.
- Cash drawer counts; routing items to more than one kitchen printer; detaching a
  modifier group from an item, or deleting groups and modifiers, except by undoing the
  change in the menu history.
