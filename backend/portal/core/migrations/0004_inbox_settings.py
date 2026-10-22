"""Three operational settings for the P8b screens, seeded as rows.

  records.due_soon_window_days   how far ahead Home looks for deadlines that are due soon. A window
                                 like this is a settings row, never a literal in code.
  records.upload_max_file_bytes  the largest file "Add a document" accepts.
  records.upload_max_files       the most files one submission may carry.

defaults.toml is only a source of rows (core.0003's docstring says why), so a deployed database
does not hold these until this migration re-runs 0002's `seed`, which inserts whatever keys are
missing and never overwrites a stored value. Reading one before then still works: `get_setting`
falls back to defaults.toml. Reversing is a no-op for the data, like 0002 and 0003.
"""

from importlib import import_module

from django.db import migrations

# Module names that start with a digit cannot be imported with the `import` statement.
seed_settings = import_module("portal.core.migrations.0002_seed_settings").seed


class Migration(migrations.Migration):
    dependencies = [("core", "0003_director_and_default_sensitivity")]
    operations = [migrations.RunPython(seed_settings, migrations.RunPython.noop)]
