"""When a message's files were extracted. One nullable column; additive.

  records.sources.ingested_at   set once, by the inbound job, when it has stored a message's
                                attachments and finished looking at it. Null until then, and null
                                for good on a source that is not mail (a hand-added document's
                                files are stored as it is created).

Why a column: the job has to know whether it has already run, and the first version inferred that
from state a PERSON can set (flagged, reviewed, has an event). Someone who opened a message in the
moment before the job ran made it look done, and its attachments were never extracted (P5 review,
F7). What the job did is now recorded as what the job did.

Grant: UPDATE (ingested_at) for the application role, which the worker runs as. The reverse drops
the column; it holds nothing that is not recomputable from the files themselves.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.sources ADD COLUMN ingested_at timestamptz;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT UPDATE (ingested_at) ON records.sources TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = "ALTER TABLE records.sources DROP COLUMN ingested_at;"


class Migration(migrations.Migration):
    dependencies = [("records_db", "0010_request_access")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
