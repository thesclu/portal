#!/usr/bin/env bash
# One image, three jobs. `web` serves the API, the built frontend and team chat's websocket;
# `worker` runs the procrastinate jobs (inbound mail, the sweeps); `migrate` applies the
# migrations as the owner role and is run once per deploy, before the new web and worker start.
set -euo pipefail
cd /app

case "${1:-web}" in
  web)
    # P23a: an ASGI server (uvicorn, several worker processes), so the same process serves HTTP
    # and the socket at /api/core/chat/socket (portal.asgi). More than one worker process needs
    # REDIS_URL for the chat channel layer: `check --deploy` refuses production without it
    # (portal.E102) unless WEB_CONCURRENCY is 1, the same variable read here (default 3 in both).
    # TLS ends at the reverse proxy, which passes the websocket upgrade (README, HANDOFF).
    exec uv run --no-sync uvicorn portal.asgi:application \
      --app-dir backend \
      --host 0.0.0.0 \
      --port "${PORT:-8000}" \
      --workers "${WEB_CONCURRENCY:-3}" \
      --lifespan off \
      --ws websockets-sansio \
      --ws-max-size 65536 \
      --timeout-graceful-shutdown "${WEB_TIMEOUT:-60}" \
      --no-server-header
    ;;
  worker)
    exec uv run --no-sync backend/manage.py procrastinate worker
    ;;
  migrate)
    # Migrations need the owner role: the app role has no DDL and cannot touch records.*.
    : "${DATABASE_MIGRATE_URL:?set DATABASE_MIGRATE_URL to the connection string of the owner role}"
    DATABASE_URL="${DATABASE_MIGRATE_URL}" exec uv run --no-sync backend/manage.py migrate --noinput
    ;;
  check)
    exec uv run --no-sync backend/manage.py check --deploy
    ;;
  *)
    exec "$@"
    ;;
esac
