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

## Stack

Python 3.11+ · FastAPI · PostgreSQL + per-device SQLite · SQLAlchemy 2.0 · Alembic.

## Develop

```bash
pip install -e ".[dev]"
pytest                       # model smoke tests (sqlite in-memory)
ruff check . && mypy src
uvicorn void_pos.main:app --reload
```

Set `VOIDPOS_DATABASE_URL` to point at Postgres; it defaults to a local instance.
