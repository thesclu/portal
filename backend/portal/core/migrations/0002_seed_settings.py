import tomllib
from pathlib import Path

from django.db import migrations

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "defaults.toml"


def seed(apps, schema_editor):
    Setting = apps.get_model("core", "Setting")
    with DEFAULTS_PATH.open("rb") as fh:
        for key, spec in tomllib.load(fh)["settings"].items():
            Setting.objects.get_or_create(
                key=key, defaults={"value": spec["value"], "description": spec.get("description", "")}
            )


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
