"""P7: what a deploy relies on. `check --deploy` is clean with production settings; the built
frontend is served from the web process with the API untouched; the container files exist and
say what the plan says."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.test import Client, override_settings

REPO = Path(__file__).resolve().parents[3]
MANAGE = REPO / "backend" / "manage.py"


def _check_deploy(**extra: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "DEBUG": "0",
        "SECRET_KEY": "deploy-check-only-not-a-secret-xxxxxxxxxxxxxxxxxxxxxxx",
        "DATABASE_URL": os.environ.get("DATABASE_URL", "postgres://x:x@localhost/x"),
        "DJANGO_SETTINGS_MODULE": "portal.settings",
        **extra,
    }
    return subprocess.run(
        [sys.executable, str(MANAGE), "check", "--deploy"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# P25a: production signs in through Keycloak under /auth on the portal's own address. The secrets
# are placeholders that pass the checks and nothing else; nothing is connected to.
PRODUCTION = {
    "PORTAL_PUBLIC_URL": "https://portal.thesclu.org",
    "KEYCLOAK_ISSUER": "https://portal.thesclu.org/auth/realms/sclu",
    "KEYCLOAK_INTERNAL_URL": "http://keycloak.invalid:8080/auth",
    "KEYCLOAK_CLIENT_SECRET": "deploy-check-only-not-a-secret",
    "KEYCLOAK_ADMIN_CLIENT_SECRET": "deploy-check-only-not-a-secret",
    "REDIS_URL": "redis://redis.invalid:6379/0",
}


def test_check_deploy_is_clean_with_production_settings() -> None:
    """A fresh interpreter, because DEBUG and the secure settings are decided at import time.
    The secret is a placeholder long enough to pass the strength check and nothing else. Since
    P23a production has a Redis address for team chat's several web processes (read, never
    connected to); since P25a, Keycloak's issuer, its back-channel address and both client
    secrets, and the repo's production Keycloak settings file passes its own checks."""
    done = _check_deploy(**PRODUCTION)
    assert done.returncode == 0, done.stderr + done.stdout
    assert "System check identified no issues (0 silenced)." in done.stdout, done.stdout


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"KEYCLOAK_ISSUER": "http://portal.thesclu.org/auth/realms/sclu"}, "portal.E110"),
        ({"KEYCLOAK_ISSUER": "https://login.elsewhere.org/realms/sclu"}, "portal.E111"),
        ({"KEYCLOAK_CLIENT_SECRET": ""}, "portal.E112"),
        ({"KEYCLOAK_ADMIN_CLIENT_SECRET": ""}, "portal.E112"),
        ({"KEYCLOAK_INTERNAL_URL": ""}, "portal.E112"),
    ],
)
def test_check_deploy_refuses_a_sign_in_that_is_not_production_shaped(change, code) -> None:
    """P25a: an http issuer, an issuer not under /auth on the portal's own address (one domain),
    or a missing back-channel address or client secret is refused."""
    done = _check_deploy(**{**PRODUCTION, **change})
    assert done.returncode != 0
    assert code in done.stderr + done.stdout


def test_the_production_keycloak_settings_file_is_checked(tmp_path) -> None:
    """P25a: `keycloak/production/keycloak.env` must keep strict hostnames, /auth, proxy headers
    from the proxy only and the admin console off the public address, and hold no secret."""
    from portal.core.keycloak.checks import PRODUCTION_ENV, production_env_problems

    public = "https://portal.thesclu.org"
    assert production_env_problems(REPO / PRODUCTION_ENV, public) == []
    bad = tmp_path / "keycloak.env"
    good = (REPO / PRODUCTION_ENV).read_text()
    bad.write_text(
        good.replace("KC_HOSTNAME_STRICT=true", "KC_HOSTNAME_STRICT=false")
        .replace(
            "KC_HOSTNAME_ADMIN=http://localhost:8080/auth",
            "KC_HOSTNAME_ADMIN=https://portal.thesclu.org/auth",
        )
        .replace("KC_HTTP_RELATIVE_PATH=/auth", "KC_HTTP_RELATIVE_PATH=/")
        + "KC_DB_PASSWORD=oops\n"
    )
    problems = production_env_problems(bad, public)
    assert any("KC_HOSTNAME_STRICT" in p for p in problems)
    assert any("admin console on the public address" in p for p in problems)
    assert any("KC_HTTP_RELATIVE_PATH" in p for p in problems)
    assert any("KC_DB_PASSWORD" in p for p in problems)
    assert production_env_problems(tmp_path / "missing.env", public) == [
        f"{tmp_path / 'missing.env'} is missing."
    ]


def test_check_deploy_refuses_several_web_processes_without_redis() -> None:
    """P23a: the image starts WEB_CONCURRENCY uvicorn workers (default 3); without REDIS_URL a
    chat message would reach only the tabs on the process that took it, so production is
    refused (portal.E102). One process alone is a warning (portal.W102); with Redis, neither."""
    production = {**PRODUCTION, "REDIS_URL": ""}
    by_default = _check_deploy(**production)
    assert by_default.returncode != 0
    assert "portal.E102" in by_default.stderr + by_default.stdout
    assert "WEB_CONCURRENCY is 3" in by_default.stderr + by_default.stdout
    two = _check_deploy(**production, WEB_CONCURRENCY="2")
    assert two.returncode != 0 and "portal.E102" in two.stderr + two.stdout
    one = _check_deploy(**production, WEB_CONCURRENCY="1")
    assert one.returncode == 0, one.stderr + one.stdout
    assert "portal.W102" in one.stderr + one.stdout and "portal.E102" not in one.stdout
    with_redis = _check_deploy(**{**production, "REDIS_URL": "redis://redis.invalid:6379/0"})
    assert with_redis.returncode == 0, with_redis.stderr + with_redis.stdout
    assert "portal.E102" not in with_redis.stdout and "portal.W102" not in with_redis.stdout


@pytest.fixture
def built_frontend(tmp_path, settings):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=root>built</div>")
    settings.FRONTEND_DIST = dist
    return dist


def test_a_frontend_path_gets_index_html_and_api_paths_do_not(db, built_frontend) -> None:
    client = Client()
    for route in ("/", "/inbox", "/requests/abc", "/settings/templates/x"):
        page = client.get(route)
        assert page.status_code == 200, route
        assert b"built" in b"".join(page.streaming_content)  # type: ignore[attr-defined]
        assert page["Cache-Control"] == "no-store"
    # The API keeps its own 404s and 401s; nothing under it falls through to the page.
    assert client.get("/api/records/no-such-route").status_code == 404
    assert client.get("/api/core/auth/me").status_code == 401
    assert client.get("/healthz").status_code == 200


@override_settings(FRONTEND_DIST=Path("/nowhere/at/all"))
def test_without_a_build_the_page_is_a_404_not_a_crash(db) -> None:
    assert Client().get("/inbox").status_code == 404


def test_the_container_files_say_what_the_plan_says() -> None:
    dockerfile = (REPO / "Dockerfile").read_text()
    entrypoint = (REPO / "docker-entrypoint.sh").read_text()
    assert "python:3.12-slim" in dockerfile and "npm run build" in dockerfile
    # P23a: the web process is an ASGI server, so it serves team chat's socket too.
    assert "uvicorn portal.asgi:application" in entrypoint and "gunicorn" not in entrypoint
    assert '--workers "${WEB_CONCURRENCY:-3}"' in entrypoint
    for job in ("web)", "worker)", "migrate)"):
        assert job in entrypoint
    assert "DATABASE_MIGRATE_URL" in entrypoint
    assert os.access(REPO / "docker-entrypoint.sh", os.X_OK)
