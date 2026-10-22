"""Outbound mail's two needs of the records schema (BUILD_PLAN P11). Additive but for one CHECK.

1. `sources.source_type` admits `sent_email`: the copy of a message the portal sent, made when the
   provider accepts it, so it sits on the request like any other message. This replaces the CHECK
   schema.sql defines, the way records_db.0007 replaced `events_channel_check`: the constraint
   name is the one Postgres gave the original, read from `pg_constraint` on 2026-10-06, dropped
   with no `IF EXISTS` so a database holding it under another name fails loudly.
2. `agency_contacts` gains `copy_on_every_request` (the contact is put in Cc on every message to
   the agency) and a bad-address mark (`bad_address_at`, `bad_address_note`), set when mail to
   the contact bounces and a person confirms it. Plain columns with defaults; existing rows are
   unaffected, and the app role's existing column grants cover them because 0002 granted the
   table, not a column list (checked: `GRANT ... ON records.agency_contacts`).

schema.sql is untouched and its sha256 does not move. The reverse puts the original CHECK back,
and fails loudly if any `sent_email` source exists, since those rows would violate it.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.sources DROP CONSTRAINT sources_source_type_check;
ALTER TABLE records.sources
  ADD CONSTRAINT sources_source_type_check
  CHECK (source_type IN ('postmark_email','imported_eml','portal_capture','scanned_mail','other',
                         'sent_email'));

ALTER TABLE records.agency_contacts
  ADD COLUMN IF NOT EXISTS copy_on_every_request boolean NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS bad_address_at timestamptz,
  ADD COLUMN IF NOT EXISTS bad_address_note text;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.sources WHERE source_type = 'sent_email';
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0013: % sent_email source(s) exist and would violate the '
      'original CHECK. Ledger rows are never deleted to make a reverse succeed.',
      blocked;
  END IF;
END
$$;
ALTER TABLE records.sources DROP CONSTRAINT sources_source_type_check;
ALTER TABLE records.sources
  ADD CONSTRAINT sources_source_type_check
  CHECK (source_type IN ('postmark_email','imported_eml','portal_capture','scanned_mail','other'));
ALTER TABLE records.agency_contacts
  DROP COLUMN IF EXISTS copy_on_every_request,
  DROP COLUMN IF EXISTS bad_address_at,
  DROP COLUMN IF EXISTS bad_address_note;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0012_production_review")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
