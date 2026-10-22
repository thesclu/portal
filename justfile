set dotenv-load := true
export PATH := "/opt/homebrew/opt/postgresql@17/bin:" + env_var("PATH")

default:
    @just --list

# Create roles, database and schemas on the local Postgres (idempotent; needs a superuser session)
db-init:
    psql -v ON_ERROR_STOP=1 -d postgres -f db/init/00_roles.sql
    psql -v ON_ERROR_STOP=1 -d sclu_portal -f db/init/01_schemas.sql

# Run Django migrations as the owner role
migrate *ARGS:
    DATABASE_URL="${DATABASE_MIGRATE_URL}" uv run backend/manage.py migrate {{ARGS}}

# Any manage.py command as the owner role (migrations, makemigrations, shell)
manage *ARGS:
    DATABASE_URL="${DATABASE_MIGRATE_URL}" uv run backend/manage.py {{ARGS}}

# Django dev server as the app role
dev-api:
    uv run backend/manage.py runserver 8000

# Vite dev server
dev-web:
    cd frontend && npm run dev

# Background job worker as the app role
worker:
    uv run backend/manage.py procrastinate worker

# Tests create their own database, so they connect as the owner role
test *ARGS:
    DATABASE_URL="${DATABASE_MIGRATE_URL}" uv run pytest {{ARGS}}

# Regenerate openapi.json and the TypeScript client from it
api-types:
    uv run backend/manage.py export_openapi_schema --api portal.api.api --output frontend/openapi.json --indent 2
    cd frontend && npm run api-types

# Populate a dev database with synthetic data (never real records)
seed:
    uv run backend/manage.py seed_synthetic

# Replay a saved Postmark inbound payload through the webhook code path
webhook-replay FILE:
    uv run backend/manage.py webhook_replay {{FILE}}

lint:
    uv run ruff check . && uv run ruff format --check . && uv run mypy backend

fmt:
    uv run ruff format . && uv run ruff check --fix .

# ---- parable gates -------------------------------------------------------

# BUILD_CHECK: lint + the full test suite against a real Postgres
build-check:
    uv run ruff check . && uv run ruff format --check .
    just test

# RUNTIME_CHECK: migrate, Django system check, boot the server, hit health and the API schema
#
# F11: `just` runs each recipe line in its own shell, so nothing could be validated across
# lines (a background server started on one line was invisible to the next); the `runserver`
# without --noreload backgrounded the autoreloader's parent, not the real server, so the
# captured PID was never the process actually listening; and a curl against a stranger already
# holding the port was indistinguishable from a curl against a server this recipe started. A
# worker who broke the app at import time still got a green smoke as long as ANYTHING held the
# port. This is a `just` shebang recipe instead: one shell for the whole body (set -euo
# pipefail), refuses to run if the port is already held, uses --noreload so the captured PID is
# the real server, and asserts that process is still alive before curling it.
smoke:
    #!/usr/bin/env bash
    set -euo pipefail
    PORT=8765
    just migrate
    DATABASE_URL="${DATABASE_MIGRATE_URL}" uv run backend/manage.py check
    if lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
      echo "smoke: port ${PORT} is already in use; refusing to run against a server this recipe did not start" >&2
      lsof -i "tcp:${PORT}" >&2
      exit 1
    fi
    uv run backend/manage.py runserver --noreload "127.0.0.1:${PORT}" > var/smoke-server.log 2>&1 &
    SERVER_PID=$!
    trap 'kill "${SERVER_PID}" 2>/dev/null || true' EXIT
    sleep 3
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
      echo "smoke: the server exited before the checks ran; its log follows" >&2
      cat var/smoke-server.log >&2
      exit 1
    fi
    curl -fsS "http://127.0.0.1:${PORT}/healthz"; echo
    # Captured, not piped into `head`. `set -o pipefail` is on, and once the schema grew past a
    # few KB (P3b added 23 drafting paths) `head -c 200` exited while curl was still writing, so
    # curl returned 23 (write error) and failed the whole gate on a response it had fetched
    # successfully. Slicing a variable keeps the -f status check and removes the race.
    SCHEMA=$(curl -fsS "http://127.0.0.1:${PORT}/api/openapi.json")
    echo "${SCHEMA:0:200}"

# P7: the production checks a deploy must pass, with placeholder secrets. Catches a settings
# change that would fail `check --deploy` before it reaches a server.
# P23a: a Redis address, which production needs for team chat with more than one web process
# (portal.E102); the check reads it and connects to nothing.
# P25a: Keycloak's issuer (https, under /auth on the portal's address), its back-channel address
# and the two client secrets (placeholders), and keycloak/production/keycloak.env's own checks
# (strict hostname, /auth, proxy headers, the admin console off the public address).
deploy-check:
    DEBUG=0 SECRET_KEY=deploy-check-only-not-a-secret-xxxxxxxxxxxxxxxxxxxxxxx PORTAL_PUBLIC_URL=https://portal.thesclu.org KEYCLOAK_ISSUER=https://portal.thesclu.org/auth/realms/sclu KEYCLOAK_INTERNAL_URL=http://keycloak.invalid:8080/auth KEYCLOAK_CLIENT_SECRET=deploy-check-only KEYCLOAK_ADMIN_CLIENT_SECRET=deploy-check-only REDIS_URL=redis://redis.invalid:6379/0 DATABASE_URL="${DATABASE_MIGRATE_URL}" uv run backend/manage.py check --deploy

# P7: build the production image (frontend built inside it)
docker-build:
    docker build -t sclu-portal .

# Print a new PORTAL_SECRETS_KEY (a Fernet key) for .env
secrets-key:
    uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# ---- P25a: sign-in on Keycloak (notes) -------------------------------------------

keycloak_compose := "docker compose --env-file var/keycloak/dev.env -f keycloak/compose.yml"

# Start the development Keycloak (its Postgres, Mailpit at http://localhost:8026), apply the realm
# from keycloak/realm/ (keycloak-config-cli; safe to run again), then write Settings' sign-in rules
# to it. The secrets are made once into var/keycloak/dev.env. Sign in at http://localhost:5173.
keycloak-up:
    #!/usr/bin/env bash
    set -euo pipefail
    keycloak/dev-secrets.sh
    mkdir -p var/keycloak/providers
    {{keycloak_compose}} up -d keycloak-db mailpit keycloak
    for i in $(seq 1 90); do
      status=$(docker inspect -f '{{{{.State.Health.Status}}' sclu-keycloak-keycloak-1 2>/dev/null || echo missing)
      [ "$status" = healthy ] && break
      sleep 2
    done
    [ "$status" = healthy ] || { echo "keycloak did not become healthy" >&2; exit 1; }
    just keycloak-config
    uv run backend/manage.py keycloak_sync --policy

# Apply keycloak/realm/*.json to the running Keycloak (keycloak-config-cli; idempotent).
keycloak-config:
    {{keycloak_compose}} run --rm keycloak-config

# Stop the development Keycloak (its data stays in the keycloak-db volume).
keycloak-down:
    {{keycloak_compose}} down

# Keycloak's log.
keycloak-logs:
    {{keycloak_compose}} logs --tail=200 -f keycloak

# Give the development seed people Keycloak accounts (the seed password; DEBUG only).
keycloak-seed:
    uv run backend/manage.py seed_synthetic --keycloak-only

# The integration tests against the real development Keycloak (skipped by `just test` when it is
# not up). Starts it first.
keycloak-test *ARGS:
    just keycloak-up
    DATABASE_URL="${DATABASE_MIGRATE_URL}" KEYCLOAK_INTEGRATION=1 uv run pytest backend/tests/keycloak {{ARGS}}

# P25a
# The sign-in pages and the mail Keycloak sends (keycloak/theme, Keycloakify; theme "sclu"): build
# the jar for Keycloak 26 from a clean install, put it in var/keycloak/providers (mounted into the
# container), restart Keycloak so it loads it, and check the sign-in page is drawn by the theme.
keycloak-theme:
    #!/usr/bin/env bash
    set -euo pipefail
    (cd keycloak/theme && npm ci --no-audit --no-fund && npm run build-keycloak-theme)
    mkdir -p var/keycloak/providers
    cp keycloak/theme/dist_keycloak/sclu-keycloak-theme.jar var/keycloak/providers/
    {{keycloak_compose}} up -d --force-recreate keycloak
    status=missing
    for _ in $(seq 1 60); do
      status=$(docker inspect -f '{{{{.State.Health.Status}}' sclu-keycloak-keycloak-1 2>/dev/null || echo missing)
      [ "$status" = healthy ] && break
      sleep 3
    done
    [ "$status" = healthy ] || { echo "keycloak did not become healthy" >&2; exit 1; }
    # Healthy can come a moment before the realm's pages are served (a 503 on the PC, 2026-10-21).
    page=""
    for _ in $(seq 1 20); do
      page=$(curl -fsS "http://localhost:8080/auth/realms/sclu/protocol/openid-connect/auth?client_id=portal&response_type=code&scope=openid&redirect_uri=http%3A%2F%2Flocalhost%3A5173%2Fapi%2Fcore%2Fauth%2Foidc%2Fcallback&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256" 2>/dev/null) && break
      sleep 3
    done
    grep -q 'themeName = "sclu"' <<<"$page" || { echo "the sign-in page is not drawn by the sclu theme" >&2; exit 1; }
    if docker logs sclu-keycloak-keycloak-1 2>&1 | grep -E "Failed to find (LOGIN|EMAIL) theme"; then exit 1; fi
    echo "keycloak-theme: Keycloak draws the sign-in pages with the sclu theme"
