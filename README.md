# SCLU Portal

The records-request portal of the Student Civil Liberties Union: requests to public agencies,
their correspondence and deadlines, drafting and filing letters, team tasks and chat.

**Stack:** Django + django-ninja on PostgreSQL 17 (row-level security), procrastinate jobs, Redis;
React 19 + Vite + TanStack Query + Tailwind; Keycloak for sign-in (OpenID Connect, passkeys).

## Run it locally

Requirements: Python with [uv](https://docs.astral.sh/uv/), Node 24+, [just](https://just.systems),
PostgreSQL 17, Redis and Docker (for Keycloak).

```bash
cp .env.example .env          # then fill in the values it describes
just db-init                  # roles and database
just migrate
just keycloak-up              # Keycloak, its database and a mail catcher; applies the realm
just keycloak-theme           # builds the sign-in pages
just seed                     # synthetic test data only
just dev-api                  # API on :8000
just dev-web                  # the app on http://localhost:5173
just worker                   # background jobs
```

Tests: `just build-check` (lint, format and the test suite), `just smoke`.

All data shipped with this repository is synthetic.
