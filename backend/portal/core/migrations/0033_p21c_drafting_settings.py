"""P21c: the drafting tool's switch, its two addresses and the usual letterhead, seeded as core.0002
seeds (DECISIONS 23: nothing a person may change lives only in code). The secrets are not
settings: they live in the server's environment only."""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0032_p23b_user_dates")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
