"""The migration graph stays honest: models match the DB, and records_db stays additive-only SQL."""

import io

from django.core.management import call_command
from django.db import connection
from django.db.migrations import RunPython, RunSQL
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder


def test_makemigrations_check_detects_no_drift(db) -> None:
    # Raises SystemExit if Django thinks a model needs a migration that doesn't exist.
    call_command("makemigrations", "--check", "--dry-run", stdout=io.StringIO())


def test_applied_migrations_cover_every_migration_on_disk(db) -> None:
    """F8: addopts no longer carries --reuse-db, but a stale test database is still possible
    (e.g. a leftover one created by hand, or a future flag). `migrate` is roll-forward only, so
    a database missing a migration that exists on disk fails structurally somewhere unrelated
    and confusing instead of here. Compare disk_migrations directly against the recorder's
    applied set so staleness announces itself by name.
    """
    disk = set(MigrationLoader(connection).disk_migrations.keys())
    applied = set(MigrationRecorder(connection).applied_migrations().keys())
    missing = disk - applied
    assert not missing, f"migrations on disk but not applied to this database: {sorted(missing)}"


# The two schema-holding apps and how many migrations each has on disk today
# (records_db 0001..0015, drafting_db 0001..0003, mail_db 0001). Zero migrations for one would
# make the loop
# below vacuously pass, so the counts are pinned: a loader that silently stops finding
# migrations fails loudly instead of reporting a clean sweep.
SCHEMA_APP_MIGRATION_COUNTS = {"records_db": 23, "drafting_db": 6, "mail_db": 2}


def test_schema_app_migrations_are_sql_or_python_runners_only(db) -> None:
    loader = MigrationLoader(connection)
    offenders = {}
    found = dict.fromkeys(SCHEMA_APP_MIGRATION_COUNTS, 0)
    for (app_label, name), migration in loader.disk_migrations.items():
        if app_label not in found:
            continue
        found[app_label] += 1
        bad_ops = [
            type(op).__name__
            for op in migration.operations
            if not isinstance(op, RunSQL | RunPython)
        ]
        if bad_ops:
            offenders[f"{app_label}.{name}"] = bad_ops
    assert found == SCHEMA_APP_MIGRATION_COUNTS
    assert offenders == {}
