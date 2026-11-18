# Git hooks

Local fast-feedback gate mirroring `.github/workflows/pr-checks.yml`.

Install (per clone):

```bash
git config core.hooksPath .githooks
```

`pre-push` checks what the push changes:

- Python (`*.py`, `pyproject.toml`, `constraints.txt`, `migrations/`): `ruff check .`,
  `mypy src`, `pytest -q`, and `alembic check` on a scratch SQLite database.
- `web/`: `npm run typecheck` and `npm test` (needs `npm ci` in `web/` once).

PostgreSQL and the Docker venue stack run only in CI. Bypass in emergencies only with
`git push --no-verify`. The authoritative gate is the workflow; this hook just saves a
round trip to CI.
