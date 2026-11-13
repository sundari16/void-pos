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
- `src/void_pos/ids.py` — ULID helpers.
- `migrations/` — Alembic.
- `tests/` — pytest; model tests run against sqlite in-memory.

## Money & time

- Money is integer **cents**; never float. Currency defaults to **AUD** (Australia-first).
- Timestamps stored timezone-aware (UTC); display in Australia/Brisbane.
