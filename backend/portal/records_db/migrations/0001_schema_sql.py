"""Apply db/records/schema.sql, byte for byte, into the `records` schema.

The file is the ground truth and is never edited. It is passed to RunSQL as a
single list element so Django hands the whole text to psycopg in one call
using the simple query protocol, which lets Postgres parse the $$-quoted
function bodies itself instead of Django's statement splitter guessing at
them. SET LOCAL is transaction-scoped, and Postgres migrations run inside one.
"""

from pathlib import Path

from django.db import migrations

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "db" / "records" / "schema.sql"
SCHEMA_SQL = SCHEMA_PATH.read_text(encoding="utf-8")


class Migration(migrations.Migration):
    initial = True
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunSQL(
            sql=[
                "CREATE SCHEMA IF NOT EXISTS records",
                "SET LOCAL search_path TO records",
                SCHEMA_SQL,
                "SET LOCAL search_path TO core, records, public",
            ],
            reverse_sql=["DROP SCHEMA IF EXISTS records CASCADE"],
        ),
    ]
