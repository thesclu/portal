"""Where Keycloak is, and the portal's two clients (P25a).

Read once, from the environment, into `settings.KEYCLOAK`:

- `ISSUER`: the realm's public address, the `iss` of every token. Production
  `https://portal.thesclu.org/auth/realms/sclu` (Keycloak under /auth on the portal's own address);
  development `http://localhost:5173/auth/realms/sclu` (Vite proxies /auth to Keycloak).
- `INTERNAL_URL`: Keycloak's base as this server reaches it (back channel: discovery, keys,
  tokens, the admin API). Production the private address (e.g. `http://keycloak:8080/auth`);
  development `http://localhost:8080/auth`. Keycloak resolves back-channel endpoints from the
  address it is asked on (`hostname-backchannel-dynamic`), so discovery fetched here names
  internal token and key endpoints and public browser ones.
- `CLIENT_ID` / `CLIENT_SECRET`: the confidential `portal` client (the sign-in itself).
- `ADMIN_CLIENT_ID` / `ADMIN_CLIENT_SECRET`: `portal-admin`, the service account for the admin API.

Secrets come from the environment and nowhere else. On a development machine (DEBUG) a missing
secret is read from `var/keycloak/dev.env`, which `keycloak/dev-secrets.sh` makes once for the
development stack, the way `var/oidc.key` was made before.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEV_ISSUER = "http://localhost:5173/auth/realms/sclu"
DEV_INTERNAL_URL = "http://localhost:8080/auth"
DEV_FILE = Path("keycloak") / "dev.env"


def _dev_values(var_dir: Path) -> dict[str, str]:
    path = var_dir / DEV_FILE
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def realm_of(issuer: str) -> str:
    """The realm's name, the last segment of `.../realms/<name>`."""
    path = urlsplit(issuer).path.rstrip("/")
    head, _, name = path.rpartition("/")
    return name if head.endswith("/realms") else ""


def from_environment(env: Any, *, debug: bool, var_dir: Path) -> dict[str, Any]:
    dev = _dev_values(var_dir) if debug else {}

    def secret(name: str, dev_name: str) -> str:
        value = str(env(name, default="") or "")
        return value or dev.get(dev_name, "")

    issuer = str(env("KEYCLOAK_ISSUER", default=DEV_ISSUER if debug else "")).rstrip("/")
    internal = str(env("KEYCLOAK_INTERNAL_URL", default=DEV_INTERNAL_URL if debug else ""))
    return {
        "ISSUER": issuer,
        "INTERNAL_URL": internal.rstrip("/"),
        "REALM": realm_of(issuer),
        "CLIENT_ID": str(env("KEYCLOAK_CLIENT_ID", default="portal")),
        "CLIENT_SECRET": secret("KEYCLOAK_CLIENT_SECRET", "KEYCLOAK_PORTAL_CLIENT_SECRET"),
        "ADMIN_CLIENT_ID": str(env("KEYCLOAK_ADMIN_CLIENT_ID", default="portal-admin")),
        "ADMIN_CLIENT_SECRET": secret(
            "KEYCLOAK_ADMIN_CLIENT_SECRET", "KEYCLOAK_ADMIN_CLIENT_SECRET"
        ),
        # Where an administrator opens Keycloak's own console (shown in Settings, never linked
        # from a public page). Production keeps it off the public proxy (notes).
        "ADMIN_CONSOLE_URL": str(
            env(
                "KEYCLOAK_ADMIN_CONSOLE_URL",
                default=f"{DEV_INTERNAL_URL}/admin/sclu/console/" if debug else "",
            )
        ),
        # How long one call to Keycloak may take before it is given up on (seconds).
        "TIMEOUT_SECONDS": float(env("KEYCLOAK_TIMEOUT_SECONDS", default=10)),
    }


def internal_realm_url(conf: Mapping[str, Any]) -> str:
    """The realm on the back channel: `<internal>/realms/<realm>`."""
    return f"{conf['INTERNAL_URL']}/realms/{conf['REALM']}"


def discovery_url(conf: Mapping[str, Any]) -> str:
    return f"{internal_realm_url(conf)}/.well-known/openid-configuration"


def configured(conf: Mapping[str, Any]) -> bool:
    """Everything the sign-in needs is set."""
    return all(conf.get(k) for k in ("ISSUER", "INTERNAL_URL", "REALM", "CLIENT_SECRET"))


def admin_configured(conf: Mapping[str, Any]) -> bool:
    return all(conf.get(k) for k in ("INTERNAL_URL", "REALM", "ADMIN_CLIENT_SECRET"))
