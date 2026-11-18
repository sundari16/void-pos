# Contributing to Void POS

Thanks for helping. Void POS is the open core of a restaurant POS, licensed under the
**GNU AGPL-3.0-or-later** (see [LICENSE](LICENSE)).

## Contributor License Agreement

Every pull request from outside the maintainers needs agreement to the
[Contributor License Agreement](CLA.md). It lets the project offer Void POS under the AGPL
and under commercial licences, which is how the open core is funded; you keep the copyright
in your work. Agree by ticking the box in the pull request template. A check on the pull
request enforces it.

## Before you start

- Read [docs/architecture.md](docs/architecture.md). The event log is the source of truth
  and the six data-model rules in [CLAUDE.md](CLAUDE.md) are non-negotiable: till-minted
  ULIDs and idempotent writes, modifiers as child lines, snapshots on order lines, loyalty
  as a ledger, payments keyed on our own id with the provider's reference beside it, and a
  location on every row (plus a business date on orders and payments).
- Money is integer cents; timestamps are stored in UTC.
- For anything larger than a fix, open an issue first so we can agree the shape.

## Working on the code

Setup is in the [README](README.md#develop). Before opening a pull request, run what CI runs:

```bash
ruff check . && mypy src && pytest
VOIDPOS_DATABASE_URL=sqlite:///./check.db alembic upgrade head && VOIDPOS_DATABASE_URL=sqlite:///./check.db alembic check
cd web && npm ci && npm run typecheck && npm test
```

`git config core.hooksPath .githooks` runs the fast subset before every push. CI also runs
the test suite on PostgreSQL and drives the full Docker install end to end.

- Every model change needs an Alembic migration (CI fails on drift).
- New behaviour needs tests; a fix needs a test that fails without it.
- Keep a pull request to one change, and say in its description what changed and how you
  tested it.
