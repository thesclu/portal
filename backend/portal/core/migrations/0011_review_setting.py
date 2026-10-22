"""P13: the one settings row behind "review before filing", seeded as core.0002 seeds."""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0010_p13_reviews_suggestions")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
