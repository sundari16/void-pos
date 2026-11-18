# Self-hosting Void POS at a venue

Void POS runs on one always-on machine on the venue's network: PostgreSQL, the API, the
web till and a nightly backup, all from `docker compose`. Tablets open the till in a
browser. Nothing leaves the building; the internet is optional.

```
 tablets ──HTTP :3000──▶ web (Next.js) ──/api──▶ api (FastAPI) ──▶ db (PostgreSQL)
                                                     │                  ▲
                                                     └─TCP :9100─▶ kitchen printer
                                                  backup ──pg_dump nightly──┘ → ./backups
```

## Requirements

- A machine that stays on during service (a small PC or NUC is plenty), on the same network
  as the tablets and the kitchen printer, with Docker and Docker Compose.
- A fixed address for that machine on the venue network (a DHCP reservation on the router),
  so tablets and the printer settings don't break when it restarts.

## Install

```bash
git clone <this repo> void-pos && cd void-pos
cp .env.example .env
```

Edit `.env` and set:

- `POSTGRES_PASSWORD`: letters and digits only (it goes inside a connection URL).
- `VOIDPOS_ADMIN_TOKEN`: 24+ characters. This is the venue admin's key; keep it private.

Both commands for generating them are in `.env.example`. Then start everything:

```bash
docker compose up -d --build
docker compose ps            # db, api and web should become "healthy"/"running"
```

The API migrates the database on every start, so the first start creates the schema.

## Set up the venue and pair a till

Use the setup CLI inside the API container:

```bash
docker compose exec api void-pos create-location --org "Madras Curry House" --name "Brisbane CBD"
docker compose exec api void-pos add-device --location <location id> --name "Front Till" --range 1000-1999
```

`add-device` prints a pairing code such as `K7QM-4XPA`, valid for 15 minutes and usable
once. On the tablet, open `http://<venue machine>:3000` and enter the code. That browser is
now the till.

- Give each till its own order-number range (they must not overlap). The numbers are the
  ones printed on kitchen tickets.
- A till that lost its storage, or a new tablet taking over: issue a fresh code with
  `void-pos pairing-code --device <id>`. Pairing again replaces the old token.
- A lost or stolen tablet: `void-pos revoke-device --device <id>` cuts it off at once.
- `void-pos list-devices --location <id>` shows every till, whether it's paired, and its
  last applied sequence number.

The same setup is available over HTTP under `/admin` with
`Authorization: Bearer <VOIDPOS_ADMIN_TOKEN>`; see `http://<venue machine>:8000/docs`.

## Menu

The quickest way in is a spreadsheet. Start from [menu-template.csv](menu-template.csv):
one row per item with `name` and `price` (in dollars), and optionally `category`,
`kitchen_name` (any script), `tax_rate` (`10%` or `0.1`) and `is_available` (`yes`/`no`).
In Excel use **Save As > CSV UTF-8** so Tamil and other scripts survive. Then:

```bash
docker compose exec -T api void-pos import-menu --location <location id> --file - --dry-run < menu.csv
docker compose exec -T api void-pos import-menu --location <location id> --file - < menu.csv
```

The dry run shows what would be created and updated. Items are matched by name, so after a
price change just edit the sheet and import it again; items missing from the sheet are left
alone. Every problem in the sheet is listed by row and nothing is applied until it's clean.

Modifier groups (spice level, extras) come from a second sheet, starting from
[modifier-template.csv](modifier-template.csv): one row per modifier with `group`,
`modifier` and `price` (the change in dollars, `0` for none, negative for a discount), and
for the group `min` and `max` (how many the customer must and may pick), an optional
`available_from` / `available_to` window, and `items`, the menu items it applies to
separated by `;`. Import it after the menu, the same way:

```bash
docker compose exec -T api void-pos import-modifiers --location <location id> --file - --dry-run < modifiers.csv
docker compose exec -T api void-pos import-modifiers --location <location id> --file - < modifiers.csv
```

Groups and modifiers match by name, so re-importing after a change updates them; nothing is
removed or detached.

Items can also be managed with the admin token (see `/docs`): one at a time
(`POST /locations/{id}/menu/items`, `PATCH /menu/items/{id}`), or several at once with
`PATCH /locations/{id}/menu/items` (a price rise, switching off a section), which applies
every edit or none. An item can carry a daily window (`available_from` / `available_to`,
local time, e.g. lunch only); outside it the till greys the item out. Tills read their own
location's menu; only the admin token can change it.

Every menu edit is recorded: `GET /locations/{id}/menu/changes` lists them newest first, and
`POST /menu/changes/{change_id}/revert` undoes the latest change to an item, group or
modifier (a deleted item comes back with its modifier groups). An undo is recorded too, so
it can be undone in turn.

## Staff and receipts

Add each manager (and other staff) with a PIN of 4 to 8 digits. The command asks for the
PIN, so it never lands in the shell history:

```bash
docker compose exec api void-pos add-staff --location <location id> --name "Asha" --role manager
docker compose exec api void-pos list-staff --location <location id>
```

Once a manager exists, voiding an order and marking a dish sold out on a till need a
manager's PIN. `deactivate-staff --staff <id>` stops someone's PIN working.

Customer receipts show the venue's details. With an ABN they are tax invoices:

```bash
docker compose exec api void-pos venue-details --location <location id> \
  --business-name "Madras Curry House" --abn "<your 11-digit ABN>" \
  --address "1 Example St, Brisbane QLD 4000" --phone "07 0000 0000" --footer "Nandri!"
```

A thermal receipt printer at the counter is optional. Set `VOIDPOS_RECEIPT_PRINTER_HOST`
(and `VOIDPOS_RECEIPT_PRINTER_COLUMNS=32` for 58 mm paper) in `.env`; without one, the
till's **Receipt** button shows the receipt to print from the tablet.

## Kitchen printer

Network ESC/POS printers listen on port 9100. Set its address in `.env` and restart:

```
VOIDPOS_KITCHEN_PRINTER_HOST=192.168.1.50
```

```bash
docker compose up -d api
```

Print a test ticket before service:

```bash
docker compose exec api void-pos print-test
```

It sends a short ticket with a Tamil line. If it says the printer can't be reached, check the
address and that port 9100 is open; if the Tamil line prints as garbage, set the font below.

If the printer is off or unreachable, **Send to kitchen** says so and leaves the order open;
tapping again once the printer is back prints it once. Items added to an order later print
on an ADDED ticket with just those items, and the table number when there is one.

Tickets in a non-Latin script (Tamil, Thai, Chinese) are printed as images. Put a font with
those glyphs in `./fonts` (for example `NotoSansTamil-Regular.ttf` from Google Fonts) and set
`VOIDPOS_KITCHEN_FONT_PATH=/fonts/NotoSansTamil-Regular.ttf`.

## Go-live checklist

Run through this the day before the first service (the Madras Curry House pilot uses it).

1. **Machine:** always on, on the same network as the tablets and printer, with a fixed
   address (a DHCP reservation on the router). Power settings never sleep it.
2. **Install:** `.env` filled in, `docker compose up -d --build`, and `docker compose ps`
   shows `db`, `api` and `web` healthy.
3. **Venue:** `create-location`, then `import-menu` with the real menu and
   `import-modifiers` with its modifier groups (dry runs first), and a look over the result
   in the till: required choices (spice level) block **Add** until picked.
4. **Printer:** `print-test` prints, including the Tamil line. If there's a receipt
   printer, a test receipt prints from the till's **Receipt** button.
5. **Staff and venue:** `add-staff` for each manager (their PINs approve voids and
   sold-out), and `venue-details` with the ABN so receipts are tax invoices.
6. **Tills:** `add-device` once per tablet, each with its own order-number range; pair each
   tablet at `http://<venue machine>:3000`.
7. **Dry run of a service:** on each till, place a dine-in order with a table number, send
   it to the kitchen, start another order, then find the table in **Open orders**, add a
   dessert and send just that (an ADDED ticket prints). Take a cash payment, then another
   paid by **Card on terminal** (take the card on the venue's own terminal as usual and
   type its receipt or approval number into the till). Split one bill two ways (**Split
   2**, one share cash, one card), print or show a receipt, mark a dish sold out and back on
   with a manager's PIN, and void one sent order with the PIN: a VOID ticket prints. The
   closeout should show cash and card separately, the GST, and the voided order.
8. **Backups:** a dump appears in `./backups`; copy it off the machine and set a habit (or a
   synced folder) for copying the nightly ones.
9. **Fallback:** keep the venue's previous way of taking orders to hand for the first services.

## Backups

The `backup` service writes a full dump to `./backups` when it starts and every night at
`BACKUP_AT` (default 03:00, venue time), keeping `BACKUP_KEEP_DAYS` days (default 14).

**Copy `./backups` off the machine regularly** (a USB drive, or a synced folder). A backup
on the same disk doesn't survive the disk.

Restore a dump (this replaces the current data):

```bash
docker compose stop api web
docker compose exec -T db pg_restore --clean --if-exists -U voidpos -d void_pos < backups/void_pos-20261118-030000.dump
docker compose start api web
```

Taking a backup right now: `docker compose restart backup`.

## Export

The owner can take everything out at any time, as JSON or CSV, with the admin token:
`GET /export/{location_id}` and `GET /export/{location_id}/csv/{orders|order_lines|payments|...}`.

## Upgrade

```bash
git pull
docker compose up -d --build
```

The API applies any new migrations on start. Take a backup first
(`docker compose restart backup`) when upgrading across versions.

## Troubleshooting

| Symptom | Check |
|---|---|
| Tablet can't load the till | `docker compose ps`; the tablet is on the venue network; the machine's address hasn't changed |
| "Can't reach the Void POS server" on the till | the `api` container is healthy (`docker compose logs api`); retrying the action is safe |
| "Kitchen printer isn't answering" | the printer is on, its address matches `VOIDPOS_KITCHEN_PRINTER_HOST`, port 9100 is open |
| Till asks to pair again | it was revoked or re-paired elsewhere; issue a new code |
| "Too many wrong pairing codes" | wait the minutes it says (ten wrong codes in ten minutes pause pairing), then enter a fresh code carefully |
| "A manager's PIN is needed" | a manager types their PIN in the prompt; add a manager with `add-staff` if there's none |
| "No receipt printer is set up" | use **Print from this device**, or set `VOIDPOS_RECEIPT_PRINTER_HOST` |
| An item is greyed out on the till | it's switched off, or outside its daily window (shown on the button); the till refreshes the menu every few minutes |
| `api` keeps restarting | `docker compose logs api`; usually `.env` values (admin token under 24 characters, wrong password) |
