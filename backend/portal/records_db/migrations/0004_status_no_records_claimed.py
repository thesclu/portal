"""A status for an agency's claim that no responsive records exist.

records-schema.md: adding a status is an insert, not a migration. It is done as
a data migration only so every environment has the row. Not terminal: the
claim usually has to be challenged, so the service layer opens a review task
when a request lands here and nothing ever closes it automatically.
"""

from django.db import migrations

INSERT = """
INSERT INTO records.status_definitions (code, label, sort_order, is_terminal)
VALUES ('no_records_claimed', 'No records claimed', 42, false)
ON CONFLICT (code) DO NOTHING;
"""

DELETE = "DELETE FROM records.status_definitions WHERE code = 'no_records_claimed';"


class Migration(migrations.Migration):
    dependencies = [("records_db", "0003_additive")]
    operations = [migrations.RunSQL(sql=[INSERT], reverse_sql=[DELETE])]
