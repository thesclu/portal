"""Apply db/drafting/drafting-schema.sql, byte for byte, into the `drafting` schema.

The file is ground truth (sha256-pinned by test_schema_sql_verbatim.py) and is
never edited. It goes to RunSQL as a single list element so Django hands the
whole text to psycopg in one call using the simple query protocol, letting
Postgres parse the two $$-quoted function bodies itself instead of Django's
statement splitter guessing at them.

Unlike records_db.0001 this sets no search_path. The drafting SQL is fully
schema-qualified, and pgcrypto lives in the `records` schema (records_db.0001
created it under `SET LOCAL search_path TO records`), so `gen_random_uuid()` in
the column defaults resolves only while `records` is still on the path. The
connection's own `search_path=core,records,public` supplies that; narrowing it
to `drafting` here would break every default in the file.
"""

from pathlib import Path

from django.db import migrations

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "db" / "drafting" / "drafting-schema.sql"
SCHEMA_SQL = SCHEMA_PATH.read_text(encoding="utf-8")


class Migration(migrations.Migration):
    initial = True
    dependencies = [("records_db", "0005_request_item_sort_order")]
    operations = [
        migrations.RunSQL(
            sql=[SCHEMA_SQL],
            reverse_sql=["DROP SCHEMA IF EXISTS drafting CASCADE"],
        ),
    ]
