"""P25c: authenticator apps are allowed by default (owner, 2026-10-20: "i want to allow both pass
key and auth app"; DECISIONS 54).

`auth.allow_authenticator_app` was seeded off by core.0035. This turns the existing row on, with
the new description, unless an administrator has already chosen a value: every change made in
Settings (`PUT /core/settings/<key>`) writes a `setting_changed` row to the activity log naming
the key, so a row for this key means someone decided, and their value stays. Without one the row
still holds the old seeded default, which is what this replaces. The description (what the switch
means) is the new one either way.

The realm follows on its own: `just keycloak-up` and the hourly sweep write Settings' sign-in rules
to Keycloak (`core.keycloak.policy`), and keycloak/realm/sclu.json's default is on too.

Not reversed: going back leaves the value as it is (an administrator can switch it off).
"""

import tomllib
from pathlib import Path

from django.db import migrations

KEY = "auth.allow_authenticator_app"
DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "defaults.toml"


def allow_by_default(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    Activity = apps.get_model("core", "Activity")
    with DEFAULTS_PATH.open("rb") as fh:
        spec = tomllib.load(fh)["settings"][KEY]
    decided = Activity.objects.filter(action="setting_changed", data__key=KEY).exists()
    row = Setting.objects.filter(pk=KEY).first()
    if row is None:
        Setting.objects.create(key=KEY, value=spec["value"], description=spec["description"])
        return
    # The description is the switch's meaning, not anyone's choice: it is always the new one.
    row.description = spec["description"]
    if not decided:
        row.value = spec["value"]
    row.save(update_fields=["value", "description", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [("core", "0035_p25a_retire_passwords_and_provider")]
    operations = [migrations.RunPython(allow_by_default, migrations.RunPython.noop)]
