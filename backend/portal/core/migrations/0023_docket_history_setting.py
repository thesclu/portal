"""P20b2: the span of the Docket's two sparklines, seeded as core.0002 seeds (DECISIONS 23:
nothing a person may change lives only in code)."""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0022_assistant_settings")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
