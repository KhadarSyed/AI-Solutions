#!/usr/bin/env sh
# Production start: apply migrations, then serve on the platform-provided port.
# Render/Fly space-split the run command (no shell operators), so the migrate-then-serve
# sequence lives here. Local docker-compose overrides `command` and doesn't use this.
set -e
echo "[start] alembic upgrade head"
alembic upgrade head
echo "[start] launching uvicorn on port ${PORT:-8002}"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8002}"
