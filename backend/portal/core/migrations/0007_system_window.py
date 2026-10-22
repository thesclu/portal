"""The System screen's one window, seeded as a row: `core.system_mail_window_days`.

Seeded the way core.0004 to 0006 seed theirs. Reversing is a no-op for the data.
"""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0006_inbound_date_window")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
