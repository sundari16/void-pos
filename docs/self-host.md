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

Add menu items with the admin token (`POST /locations/{id}/menu/items`, see `/docs`). Tills
read their own location's menu; only the admin token can change it.

## Kitchen printer

Network ESC/POS printers listen on port 9100. Set its address in `.env` and restart:

```
VOIDPOS_KITCHEN_PRINTER_HOST=192.168.1.50
```

```bash
docker compose up -d api
```

If the printer is off or unreachable, **Send to kitchen** says so and leaves the order open;
tapping again once the printer is back prints it once.

Tickets in a non-Latin script (Tamil, Thai, Chinese) are printed as images. Put a font with
those glyphs in `./fonts` (for example `NotoSansTamil-Regular.ttf` from Google Fonts) and set
`VOIDPOS_KITCHEN_FONT_PATH=/fonts/NotoSansTamil-Regular.ttf`.

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
| `api` keeps restarting | `docker compose logs api`; usually `.env` values (admin token under 24 characters, wrong password) |
