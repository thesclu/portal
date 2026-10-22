"""P19a: the two presence windows, seeded as core.0002 seeds."""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0018_presence")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
