# The SCLU Portal, one image for the web process, the job worker and migrations (P7).
# `docker-entrypoint.sh web|worker|migrate` picks which. Build: `docker build -t sclu-portal .`

# ---- the frontend, built once ---------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- the backend -----------------------------------------------------------------------------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app
# Dependencies first, so a code change does not reinstall them.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --frozen --no-dev
COPY backend/ backend/
COPY db/ db/
COPY docker-entrypoint.sh ./
COPY --from=frontend /app/frontend/dist frontend/dist
# Django's own static files (the admin) go through WhiteNoise's manifest storage. The build has
# no database and no real secret, so both are placeholders here and only here.
RUN DEBUG=0 SECRET_KEY=build-only-not-a-secret-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx \
    DATABASE_URL=postgres://x:x@localhost/x \
    uv run --no-sync backend/manage.py collectstatic --noinput
RUN useradd --create-home --uid 1000 portal && chown -R portal:portal /app
USER portal
EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["web"]
