"""What a production deploy of the sign-in must have (P25a), under `manage.py check --deploy`
(`just deploy-check`).

The portal side: Keycloak's issuer is an https address under /auth on the portal's own address
(one domain), the back-channel address and both client secrets are set. The Keycloak side: the
production environment file this repo ships for the server (`keycloak/production/keycloak.env`,
notes) keeps strict hostname checking, the /auth path, proxy headers from the proxy
only, and the admin console off the public address.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.core import checks

PRODUCTION_ENV = Path("keycloak") / "production" / "keycloak.env"
# What the production Keycloak must be started with (exact values, or None: set at all).
REQUIRED = {
    "KC_HOSTNAME": None,
    "KC_HOSTNAME_STRICT": "true",
    "KC_HTTP_RELATIVE_PATH": "/auth",
    "KC_PROXY_HEADERS": "xforwarded",
    "KC_PROXY_TRUSTED_ADDRESSES": None,
    "KC_HTTP_ENABLED": "true",
    "KC_HOSTNAME_BACKCHANNEL_DYNAMIC": "true",
    "KC_HEALTH_ENABLED": "true",
    "KC_DB": "postgres",
    "KC_DB_URL": None,
    "KC_DB_USERNAME": None,
}


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def production_env_problems(path: Path, public_url: str) -> list[str]:
    """What is wrong with the production Keycloak environment file, in words."""
    if not path.is_file():
        return [f"{path} is missing."]
    values = _read_env(path)
    problems = []
    for name, wanted in REQUIRED.items():
        got = values.get(name, "")
        if not got:
            problems.append(f"{name} is not set.")
        elif wanted is not None and got != wanted:
            problems.append(f"{name} is {got!r}, not {wanted!r}.")
    hostname = values.get("KC_HOSTNAME", "")
    if hostname and hostname != f"{public_url}/auth":
        problems.append(f"KC_HOSTNAME is {hostname!r}, not the portal's address + /auth.")
    if hostname and not hostname.startswith("https://"):
        problems.append("KC_HOSTNAME is not an https:// address.")
    admin = values.get("KC_HOSTNAME_ADMIN", "")
    if admin and urlsplit(admin).hostname == urlsplit(hostname).hostname:
        problems.append("KC_HOSTNAME_ADMIN puts the admin console on the public address.")
    secrets = ("KC_BOOTSTRAP_ADMIN_PASSWORD", "KC_BOOTSTRAP_ADMIN_CLIENT_SECRET", "KC_DB_PASSWORD")
    for name in secrets:
        if values.get(name):
            problems.append(f"{name} has a value in the file; it belongs in the server's secrets.")
    return problems


@checks.register(checks.Tags.security, deploy=True)
def keycloak_deploy_checks(app_configs: Any, **kwargs: Any) -> list[checks.CheckMessage]:
    if settings.DEBUG:
        return []
    conf = settings.KEYCLOAK
    public = settings.PORTAL["PUBLIC_URL"]
    found: list[checks.CheckMessage] = []
    issuer = str(conf.get("ISSUER", ""))
    if not issuer.startswith("https://"):
        found.append(
            checks.Error(
                "Keycloak's issuer (KEYCLOAK_ISSUER) is not an https:// address.",
                hint="https://portal.thesclu.org/auth/realms/sclu",
                id="portal.E110",
            )
        )
    elif not issuer.startswith(f"{public}/auth/realms/"):
        found.append(
            checks.Error(
                "Keycloak's issuer is not under /auth on the portal's own address.",
                hint=f"{public}/auth/realms/sclu (one domain; PORTAL_PUBLIC_URL is {public}).",
                id="portal.E111",
            )
        )
    for name, setting in (
        ("KEYCLOAK_INTERNAL_URL", "INTERNAL_URL"),
        ("KEYCLOAK_CLIENT_SECRET", "CLIENT_SECRET"),
        ("KEYCLOAK_ADMIN_CLIENT_SECRET", "ADMIN_CLIENT_SECRET"),
    ):
        if not conf.get(setting):
            found.append(
                checks.Error(
                    f"{name} is not set: nobody can sign in, or the portal cannot keep Keycloak "
                    "in line with Users.",
                    hint="Set it in the server's environment (notes).",
                    id="portal.E112",
                )
            )
    for problem in production_env_problems(settings.REPO_DIR / PRODUCTION_ENV, public):
        found.append(
            checks.Error(
                f"Keycloak's production settings: {problem}",
                hint="keycloak/production/keycloak.env; see notes.",
                id="portal.E113",
            )
        )
    return found
