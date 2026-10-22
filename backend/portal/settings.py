"""Settings come from the environment, plus a .env file at the repo root in development.

Two database roles connect to one database. DATABASE_URL is the app role the web
server and the worker use; the justfile points migrations and tests at the owner
role instead. Every connection carries search_path=core,records,public, so
Django's own tables land in `core` and the records schema stays exactly where
schema.sql put it.
"""

import sys
from pathlib import Path

import environ

from portal.core.chat import layers as chat_layers
from portal.core.keycloak import config as keycloak_config

BASE_DIR = Path(__file__).resolve().parent.parent  # backend/
REPO_DIR = BASE_DIR.parent

env = environ.Env(DEBUG=(bool, False))
environ.Env.read_env(str(REPO_DIR / ".env"))  # absent in production; harmless

DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY", default="dev-only-change-me") if DEBUG else env("SECRET_KEY")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
if DEBUG and "host.docker.internal" not in ALLOWED_HOSTS:
    # P25a: the development Keycloak runs in a container and reaches this server's back-channel
    # logout as host.docker.internal (keycloak/compose.yml). Production names its own address.
    ALLOWED_HOSTS.append("host.docker.internal")

INSTALLED_APPS = [
    # P22: daphne first, so `manage.py runserver` is daphne's and serves the ASGI application
    # (HTTP and team chat's websocket) in development. Production runs an ASGI server too.
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "ninja",
    "channels",
    "procrastinate.contrib.django",
    "portal.core",
    "portal.records_db",
    "portal.records",
    "portal.drafting_db",
    "portal.drafting",
    "portal.mail_db",
    "portal.mail",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves the built frontend and Django's own static files from the web process (P7). In
    # development Vite serves the frontend itself and this has nothing to serve.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # P25a: a portal session lives exactly as long as its Keycloak session (refreshed in the
    # background; ended when Keycloak says so) and its idle limit is Settings' session length.
    "portal.core.keycloak.sessions.KeycloakSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # P21c: the pages may frame only the portal itself and, while it is in use, the drafting tool.
    "portal.drafting.middleware.FrameSourceMiddleware",
]

ROOT_URLCONF = "portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "portal.wsgi.application"
# P22: team chat's live delivery is a websocket, so the site is an ASGI application
# (`portal/asgi.py`: Django for HTTP, Channels for the socket). Since P23a the production web
# process is an ASGI server too (uvicorn, `docker-entrypoint.sh web`). The WSGI path stays
# importable for anything that still serves HTTP only (it simply has no socket; the client polls).
ASGI_APPLICATION = "portal.asgi.application"

# Where a message posted in one process reaches the sockets held by another. Redis when
# REDIS_URL is set (production, and a development machine running it as a service); otherwise
# the in-memory layer, which only reaches sockets in the same process: enough for one dev server
# and for the tests, never for several workers.
REDIS_URL = env("REDIS_URL", default="")
# P23a: how many web worker processes the image's `web` mode starts (docker-entrypoint.sh reads
# the same variable, with the same default). More than one needs REDIS_URL: `check --deploy`
# refuses production without it (portal.E102, portal/core/chat/checks.py).
WEB_CONCURRENCY = env.int("WEB_CONCURRENCY", default=3)
# P23a: built by `portal.core.chat.layers`, which also sets the Redis read timeout above
# channels-redis's blocking pop (with redis-py 8's 5-second default an idle socket could die).
CHANNEL_LAYERS = chat_layers.channel_layers(REDIS_URL)


def _system_db_context() -> bool:
    """Whether every connection of this process is the portal's own work (P17, records_db.0017):
    the job worker and management commands (seed, replay, shell), which run `manage.py` with any
    command but the dev server. A web server (uvicorn, runserver) gets no context at connection
    level; the session auth sets `web` per request. Tests get neither and set what they test.
    `PORTAL_DB_CONTEXT_SYSTEM=1` says so explicitly for a process started another way."""
    if env.bool("PORTAL_DB_CONTEXT_SYSTEM", default=False):
        return True
    argv = sys.argv
    return (
        len(argv) > 1
        and argv[0].endswith("manage.py")
        and argv[1] not in ("runserver", "test", "check")
    )


_DB_OPTIONS = "-c search_path=core,records,public" + (
    " -c app.context=system" if _system_db_context() else ""
)

DATABASES = {
    "default": {
        **env.db("DATABASE_URL"),
        "ATOMIC_REQUESTS": True,
        # No persistent connections by default, in production too since P23a. Under ASGI (the
        # dev server since P22, uvicorn in production since P23a) each request's sync code runs
        # in a thread of its own, and a connection held past the request outlives its thread:
        # in development they piled up until Postgres refused new ones (P20b, P20d, under the
        # threaded dev server). A pool (Django's `OPTIONS["pool"]`) is the way to reuse them.
        "CONN_MAX_AGE": env.int("CONN_MAX_AGE", default=0),
        "OPTIONS": {"options": _DB_OPTIONS},
    }
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# The records and drafting apps' models are unmanaged views over their SQL
# schemas. Their DDL lives only in portal.records_db / portal.drafting_db, so
# makemigrations never touches either.
MIGRATION_MODULES = {"records": None, "drafting": None}

AUTH_USER_MODEL = "core.User"
# P25a: the portal holds no passwords (Keycloak does; its realm's password policy is the old
# validators' rules: 12 characters, not the email, not on Django's list of common passwords).
# Nobody signs in with a password here, so no validator and no password backend is configured.
AUTH_PASSWORD_VALIDATORS: list[dict[str, str]] = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"  # storage is absolute instants; display zones are per user
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = REPO_DIR / "var" / "static"
# The built frontend. WhiteNoise serves its files at the site root (`/assets/...`, the favicon);
# `portal.urls` hands every non-API path, the root included, its index.html so the client-side
# router can take over. Absent in development, where Vite serves the frontend on its own port.
FRONTEND_DIST = REPO_DIR / "frontend" / "dist"
WHITENOISE_ROOT = str(FRONTEND_DIST) if FRONTEND_DIST.is_dir() else None

MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(REPO_DIR / "var" / "media")))
if not MEDIA_ROOT.is_absolute():
    MEDIA_ROOT = (REPO_DIR / MEDIA_ROOT).resolve()

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
        "OPTIONS": {"location": str(MEDIA_ROOT)},
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
if env("STORAGE_S3_BUCKET", default=""):
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("STORAGE_S3_BUCKET"),
            "endpoint_url": env("STORAGE_S3_ENDPOINT", default=None),
            "region_name": env("STORAGE_S3_REGION", default=None),
            "default_acl": "private",
            "file_overwrite": False,
        },
    }

# P15a: a session ends after this long without a request ("signed out after 8 hours"). P25a: the
# length is an administrator's (Settings → System → Sign-in, `auth.session_hours`, written to
# Keycloak too); each portal session is given it when it starts and on every request
# (`portal.core.keycloak.sessions`). SESSION_HOURS is only the cookie's outer bound before that.
# An invite's email link works for a week (in hours, so the day-count scanner, which cannot tell
# an invite's lifetime from a statutory clock, leaves it alone).
SESSION_COOKIE_AGE = env.int("SESSION_HOURS", default=8) * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
INVITE_HOURS = 7 * 24

# Session cookie auth for the SPA. The CSRF cookie must be readable by scripts
# so the client can echo it in the X-CSRFToken header.
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=["http://localhost:5173"])
if not DEBUG:
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# P25a: the limit on sign-in attempts (P7) is Keycloak's brute-force detection now (the realm
# file: five failures in fifteen minutes lock the address for fifteen minutes).

# Job modules are named jobs.py, not tasks.py, because "tasks" is a ledger table.
PROCRASTINATE_AUTODISCOVER_MODULE_NAME = "jobs"

PORTAL = {
    "INBOUND_EMAIL_DOMAIN": env("INBOUND_EMAIL_DOMAIN", default="requests.thesclu.org"),
    "POSTMARK_SERVER_TOKEN": env("POSTMARK_SERVER_TOKEN", default=""),
    "POSTMARK_INBOUND_SECRET": env("POSTMARK_INBOUND_SECRET", default=""),
    # P19g: stored agency portal logins are encrypted with this key (a Fernet key).
    "SECRETS_KEY": env("PORTAL_SECRETS_KEY", default=""),
    # P11. Off: every send goes through the provider's test token, which accepts and discards.
    # Nothing leaves this system for a real inbox until this is on, whatever the token above.
    "MAIL_SEND_ENABLED": env.bool("MAIL_SEND_ENABLED", default=False),
    "POSTMARK_MESSAGE_STREAM": env("POSTMARK_MESSAGE_STREAM", default="outbound"),
    # P15a: the address the portal's own mail (invites, resets) comes from, and the address of
    # the site, for the links in it.
    "SYSTEM_EMAIL_FROM": env(
        "SYSTEM_EMAIL_FROM",
        default="portal@" + env("INBOUND_EMAIL_DOMAIN", default="requests.thesclu.org"),
    ),
    "PUBLIC_URL": env("PORTAL_PUBLIC_URL", default="http://localhost:5173").rstrip("/"),
}

# ---- P21c: the drafting tool (the SCLU fork of Mike), its seam (portal.drafting.mike) ----------
#
# Whether drafting uses the fork is an administrator's switch in Settings → System
# (`drafting.fork_enabled`, off by default), and so are its two addresses; these variables are only
# the addresses' defaults, for when Settings leaves them empty. The two shared secrets live here
# and nowhere else (never in the database, never on a screen; Settings says only whether each is
# set). With the switch off, or any secret missing, the portal drafts in its own editor as before.
# P25b: the tool signs people in through Keycloak's `drafting` client (its secret is Keycloak's and
# the tool's, not the portal's), and Keycloak's back-channel logout ends its sessions, so the third
# secret (DRAFTING_END_SESSIONS_SECRET, the end-sessions call's) is gone.
DRAFTING = {
    # The fork's API as this server reaches it: http://backend:3001 on the suite's Compose
    # network, https://portal.thesclu.org/api/draft through nginx, http://localhost:3000/api on a
    # laptop (SCLU.md 14.4).
    "API_URL": env("DRAFTING_API_URL", default="").rstrip("/"),
    # The fork's pages as a browser reaches them, for the frames (stage 2, Templates): the
    # frames' origin and the Content-Security-Policy's frame-src. http://localhost:3000 locally.
    "WEB_URL": env("DRAFTING_WEB_URL", default="").rstrip("/"),
    # = the fork's SCLU_PORTAL_SERVICE_SECRET: signs every service call (SCLU.md 14.4).
    "SERVICE_SECRET": env("DRAFTING_SERVICE_SECRET", default=""),
    # = the fork's SCLU_WEBHOOK_SECRET: what the fork signs its webhooks with (SCLU.md 9.3).
    "WEBHOOK_SECRET": env("DRAFTING_WEBHOOK_SECRET", default=""),
    # How long one call to the fork may take before it is given up on.
    "TIMEOUT_SECONDS": env.float("DRAFTING_TIMEOUT_SECONDS", default=30.0),
}

# ---- P25a: sign-in is Keycloak's; the portal is an OpenID Connect client (DECISIONS 51, 52) ------
#
# The browser never holds a token: the server does the authorization code flow (PKCE, state,
# nonce) and keeps its own session cookie, with the tokens in the session (backend for frontend).
# Where Keycloak is (its public issuer and the address this server reaches it on) and the two
# client secrets come from the environment only; on a development machine they default to the
# development stack (keycloak/compose.yml) and its var/keycloak/dev.env. Everything an
# administrator may change about signing in is a Settings row (`auth.*`), written to Keycloak.
KEYCLOAK = keycloak_config.from_environment(env, debug=DEBUG, var_dir=REPO_DIR / "var")
# A person not signed in is sent to the SPA's sign-in screen, which goes on to Keycloak.
LOGIN_URL = "/sign-in"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
