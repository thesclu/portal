import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from portal.core.models import Setting

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "defaults.toml"


@lru_cache(maxsize=1)
def defaults() -> dict[str, dict[str, Any]]:
    with DEFAULTS_PATH.open("rb") as fh:
        return tomllib.load(fh)["settings"]


def get_setting(key: str, default: Any = None) -> Any:
    """The stored value, else the default from defaults.toml, else `default`."""
    try:
        return Setting.objects.get(pk=key).value
    except Setting.DoesNotExist:
        return defaults().get(key, {}).get("value", default)


def organisation_name() -> str:
    """The organisation's name (`org.name`): Keycloak's `sclu_org` for each person
    (`portal.core.keycloak.admin.attributes_for`) and the drafting tool's organisation label."""
    return str(get_setting("org.name") or "").strip()


def org_short_name() -> str:
    """The organisation's short name (`org.short_name`), or the initials of its name when that is
    empty; empty only when both are. Used in file names and in the portal's own name in its mail
    ("<short name> Portal")."""
    short = str(get_setting("org.short_name") or "").strip()
    if short:
        return short
    words = [w for w in str(get_setting("org.name") or "").split() if w]
    return "".join(w[0].upper() for w in words)


def seed_defaults() -> int:
    """Insert any missing keys from defaults.toml. Never overwrites a stored value."""
    created = 0
    for key, spec in defaults().items():
        _, was_created = Setting.objects.get_or_create(
            key=key, defaults={"value": spec["value"], "description": spec.get("description", "")}
        )
        created += int(was_created)
    return created
