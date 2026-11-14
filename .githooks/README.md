# Git hooks

Local fast-feedback gate mirroring `.github/workflows/pr-checks.yml`.

Install (per clone):

```bash
git config core.hooksPath .githooks
```

`pre-push` runs `ruff check .` and `pytest -q` before a push that carries Python
changes. Bypass in emergencies only with `git push --no-verify`.

The authoritative gate is the PR workflow; this hook just saves a round trip to CI.
