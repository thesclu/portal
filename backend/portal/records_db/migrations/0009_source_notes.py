"""Notes on a message, before the message belongs to any request. A new table; additive.

Why a table and not a `note` event: the Inbox lets a person write a note on a message that has not
been attached to anything yet ("who is this from, call them first"), and `events.request_id` is NOT
NULL, so an event cannot hold it. A request's Notes tab is the union of its `note` events and the
source notes of every source attached to it; that union is a query (BUILD_PLAN rule 3), and nothing
here is copied onto an event.

  records.source_notes
    source_id       the message the note is about
    author_user_id  who wrote it; NOT NULL, because a note nobody wrote is not a note
    body            the text
    voided_at / void_reason
                    a mistaken note is voided, never deleted (non-negotiable 6)

The index is the one the Notes pane reads: one message's notes in the order they were written. It is
named here exactly as Postgres would have named it, so the migration reads as the SQL it is.

Grants (0002's `ON ALL TABLES` covered only the tables that existed then, so a new table has no
privileges at all until its own migration writes them -- BUILD_PLAN rule 4): SELECT, INSERT, and
UPDATE on (voided_at, void_reason) only. Never DELETE, and the text of a note is not editable: to
correct one, void it and write another, which leaves what was believed on the record.

The reverse drops the table, and FAILS LOUDLY, by an explicit exception that names what blocks it,
if any note exists. Silently deleting ledger rows to make a reverse succeed is the one thing this
system never does (the same rule records_db.0007's reverse follows). `schema.sql` is untouched and
its sha256 does not move; the records placement pin becomes 18 in the same commit.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE records.source_notes (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id       uuid NOT NULL REFERENCES records.sources (id),
  author_user_id  uuid NOT NULL REFERENCES records.users (id),
  body            text NOT NULL,
  voided_at       timestamptz,
  void_reason     text,
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX source_notes_source_id_created_at_idx
  ON records.source_notes (source_id, created_at);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.source_notes TO portal_app;
    GRANT UPDATE (voided_at, void_reason) ON records.source_notes TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.source_notes;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0009: % note(s) on messages exist in records.source_notes. '
      'Ledger rows are never deleted to make a reverse succeed.',
      blocked;
  END IF;
END
$$;

DROP TABLE records.source_notes;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0008_v2_rows")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
