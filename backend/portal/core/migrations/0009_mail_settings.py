"""Outbound mail's two settings rows (P11): the undo-send window and the organisation's signature.
Seeded the way core.0004 to 0007 seed theirs. Reversing is a no-op for the data."""

from importlib import import_module

from django.db import migrations

seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0008_sign_in_attempts")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
