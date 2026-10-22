"""Apply db/mail/mail-schema.sql, byte for byte, into the `mail` schema.

The file is ground truth (sha256-pinned by test_schema_sql_verbatim.py) and is never edited. It
goes to RunSQL as a single list element, as drafting_db.0001 does, so Postgres parses the
$$-quoted function body itself. It depends on the records and drafting migrations its foreign
keys point at, and on records_db.0013, which admits the `sent_email` source type the seam writes.
"""

from pathlib import Path

from django.db import migrations

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "db" / "mail" / "mail-schema.sql"
SCHEMA_SQL = SCHEMA_PATH.read_text(encoding="utf-8")


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        ("records_db", "0013_sent_mail_and_contacts"),
        ("drafting_db", "0003_shell_layout"),
    ]
    operations = [
        migrations.RunSQL(sql=[SCHEMA_SQL], reverse_sql=["DROP SCHEMA IF EXISTS mail CASCADE"]),
    ]
