"""One operational setting for inbound mail, seeded as a row.

  records.inbound_max_payload_bytes  the largest delivery the Postmark webhook will read. A message
                                     with attachments arrives as one JSON body with the files
                                     base64-encoded inside it, far past Django's default cap on a
                                     request body, so the webhook reads the body itself up to this.

Seeded the way core.0004 seeds its rows: 0002's `seed` inserts whatever keys are missing and never
overwrites a stored value. Reversing is a no-op for the data.
"""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0004_inbox_settings")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
