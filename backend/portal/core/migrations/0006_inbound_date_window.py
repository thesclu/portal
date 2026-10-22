"""One more operational setting for inbound mail, seeded as a row.

  records.inbound_date_window_hours  how long before it arrived a delivered message may say it
                                     was sent and still be believed (see inbound/ingest.py).

Seeded the way core.0004 and 0005 seed theirs. Reversing is a no-op for the data.
"""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0005_inbound_settings")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
