"""Design v2's two additions to `core`.

1. `AppRole.is_director`: the marker for "can sign off" (DECISIONS, "Design v2"). Not a fourth
   role. Defaults to false, so nobody holds it until an admin gives it; `bootstrap_admin` gives it
   to the first admin it creates.
2. `records.default_sensitivity`: a new row in defaults.toml. defaults.toml is only a source of
   rows, so a deployed database does not get one until this migration re-runs 0002's `seed`, which
   inserts whatever keys are missing and never overwrites a stored value. Re-running that exact
   function (rather than copying its body) is what keeps the two from drifting apart.

Reversing is a no-op for the data, like 0002: the row is harmless without this migration, and
deleting it would throw away a value an admin may have chosen.
"""

from importlib import import_module

from django.db import migrations, models

# Module names that start with a digit cannot be imported with the `import` statement.
seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0002_seed_settings")]
    operations = [
        migrations.AddField(
            model_name="approle",
            name="is_director",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(seed_settings, migrations.RunPython.noop),
    ]
