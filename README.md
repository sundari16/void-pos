# Void POS

An **open-core, software-only, local-first** restaurant POS. Runs on the venue's own
tablets and ESC/POS printers — no hardware business, ever.

> Working name. Positioning, scope and the competitive rationale live in the
> [gap-analysis spec](https://claude.ai/artifact/2496d71p8gryuyqh7544AV) and the
> [architecture decisions](https://claude.ai/artifact/9pUAUbT6joaf7MLbwRRfAY).

## What this repo is

The **open core**: the order pipeline, catalog, local-first sync, kitchen printing,
basic dine-in/online, and full data export. Commercial modules (hosted ops, Stripe
Connect platform, voice AI, aggregators, marketing) live elsewhere.

License: **AGPL-3.0-or-later** (see `LICENSE`).

## Architecture in one line

Every mutation is an **append-only event** with a device-generated ULID and a
per-device sequence; `order`, `order_line`, `payment` and `loyalty_ledger` are
**projections** of that log. This is what makes offline-first and conflict-safe sync
possible without a sync vendor.

See the six non-negotiable data-model decisions in the architecture doc.

How it fits together (deployment, write path, auth, errors):
[docs/architecture.md](docs/architecture.md).

## Stack

Python 3.11+ · FastAPI · PostgreSQL (SQLite for tests and quick dev) · SQLAlchemy 2 ·
Alembic · Next.js web till · Docker Compose for venue installs.

## Run it at a venue

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and VOIDPOS_ADMIN_TOKEN
docker compose up -d --build
docker compose exec api void-pos create-location --org "My Venue" --name "Main"
docker compose exec api void-pos add-device --location <id> --name "Till 1" --range 1000-1999
```

Open `http://<venue machine>:3000` on a tablet and enter the pairing code. Printer,
backups, restore and upgrades: [docs/self-host.md](docs/self-host.md).

## Develop

```bash
pip install -c constraints.txt -e ".[dev,print]"
ruff check . && mypy src && pytest     # tests run on in-memory SQLite
python scripts/seed_dev.py             # a dev venue in ./void_pos_dev.db + a pairing code
VOIDPOS_DATABASE_URL=sqlite:///./void_pos_dev.db uvicorn void_pos.main:app --reload
```

Then run the web till from `web/` (see [web/README.md](web/README.md)).

- Run the suite on PostgreSQL: `VOIDPOS_TEST_DATABASE_URL=postgresql+psycopg://... pytest`.
- Against a local PostgreSQL, migrate first: `alembic upgrade head` (the URL comes from
  `VOIDPOS_DATABASE_URL`, default `postgresql+psycopg://localhost/void_pos`).
- `python scripts/smoke_test.py --api http://localhost:8000 --admin-token ...` drives a
  running API end to end.
- `git config core.hooksPath .githooks` installs the pre-push checks.
- After changing dependencies: `pip freeze --exclude-editable > constraints.txt`.
