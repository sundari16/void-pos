# Void POS — Project Instructions

Inherits the shared conventions in `C:\void-dev\CLAUDE.md` (branching, CI gates,
tech defaults, infra rules). This file adds only what is specific to Void POS.

## What this is

Open-core, software-only, local-first restaurant POS. This repo is the **open core**
(AGPL-3.0-or-later). Commercial modules (hosted ops, Stripe Connect platform, voice
AI, aggregators, marketing) are out of this repo.

- Gap-analysis spec: https://claude.ai/artifact/2496d71p8gryuyqh7544AV
- Architecture decisions: https://claude.ai/artifact/9pUAUbT6joaf7MLbwRRfAY

## Non-negotiable data-model rules

The event log is the source of truth; aggregates are projections. When adding or
changing models, keep all six:

1. **Device-generated IDs** — ULID primary keys, idempotent writes, per-device
   order-number ranges. Never server-assigned ids on the write path.
2. **Modifiers as child order lines** — `OrderLine.parent_line_id` + quantity + price
   snapshot. Never a flat list of modifier ids.
3. **Snapshots on order lines** — name, price, tax, category copied at sale time;
   never read live from the catalog.
4. **Loyalty as a ledger** — balance is `SUM(points_delta)`, never a stored field.
5. **Payments as provider + external ref** — never key on a Stripe id.
6. **Location on every row; business date on every order and payment.**

## Layout

- `src/void_pos/models/` — SQLAlchemy 2.0 models (the schema above).
- `src/void_pos/events/` — `ingest_event`: validate, append, project. Both write paths
  (till commands in `commands.py`, device sync in `sync/`) go through it.
- `src/void_pos/api/` — thin HTTP layer: `auth.py` (admin token vs paired till),
  `errors.py` (one JSON error shape), routes. Business rules live in the services.
- `src/void_pos/devices/` — locations, tills, pairing; `cli.py` — `void-pos` setup CLI.
- `src/void_pos/ids.py` — ULID helpers; `clock.py` — UTC storage, location-local dates.
- `migrations/` — Alembic. Every model change needs a migration; CI runs `alembic check`.
- `tests/` — pytest on SQLite; `tests/support.py` has the shared helpers.
  `VOIDPOS_TEST_DATABASE_URL` runs the suite on PostgreSQL.
- `web/` — Next.js till. `web/src/lib/` is pure and unit tested (`npm test`).
- `docs/architecture.md` (engineering view), `docs/self-host.md` (venue install).

## Deployment

Self-hosted only until the project is commercial: one venue machine running
`docker compose` (Postgres, API, web, nightly backups) — the Madras Curry House pilot and
the open core's self-host path. No GCP; the workspace's Cloud SQL / Cloud Build / Terraform
defaults don't apply here yet.

Target topology (decided 18 Nov 2026): cloud system of record + local-first React Native
tills (on-device SQLite + outbox, direct printing, hub-mode relay when the internet drops).
Design offline and failover work around that, not around a server per venue.

## Money & time

- Money is integer **cents**; never float. Currency defaults to **AUD** (Australia-first).
- Timestamps stored timezone-aware (UTC); display in Australia/Brisbane.
