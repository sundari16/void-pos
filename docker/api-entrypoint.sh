#!/bin/sh
# Bring the schema up to date, then serve. Migrating here means upgrading a venue is
# just `docker compose up -d --build`. Alembic's version table makes this a no-op when
# the database is already current.
set -eu

alembic upgrade head
exec uvicorn void_pos.main:app --host 0.0.0.0 --port 8000
