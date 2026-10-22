"""The columns design v2's slice-1 screens need. Additive only; schema.sql is untouched.

Every column is nullable or defaulted, so no existing row needs a backfill and no existing insert
breaks. Shaped after 0003 and 0005, including the column-level grants: 0002's `GRANT ... ON ALL
TABLES` covered only the columns that existed when it ran, and portal_app can UPDATE a column only
if a grant names it.

  requests        assigned_to_user_id        who is working it (requester_user_id is who it is for)
  request_items   provision                  the rules-package provision this item has its own
                                             clock under, so file_request computes the same
                                             deadlines whichever door files it
  tasks           assigned_to_user_id        Mine / Everyone, Reassign
                  created_by_user_id         a person made it
                  created_by_system          or a named process did ("Created by the system")
  attachments     page_count                 "34 pp"; set at ingest, null when not countable
  agencies        short_name                 feeds {{AGENCY_ABBR}}; one column for what the design
                                             labels "Short name" in two dialogs and "Abbreviation"
                                             on the detail page
                  recipient_office, mailing_street, mailing_city_state_zip
                                             the postal address the P3b gate left open
                  filing_method              email | portal | mail, checked in the service
                  portal_kind                the label beside a portal ("GovQA", "Web form")
                  request_email_verified_at  when someone last confirmed the request address works
  agency_contacts is_primary                 one primary per agency
  sources         flag_reason, assigned_to_user_id, resolution, resolution_note
                                             the Inbox buckets, assigning a message, "Mark handled
                                             without attaching"
  fees            description, payment_method

Two deliberate choices a reviewer should see:

1. `tasks.created_by_user_id` and `tasks.created_by_system` get NO UPDATE grant. Who created a task
   is provenance and is written once at insert, the same footing as `deadlines.rule_ref` in 0003
   (HANDOFF: "rule_ref can only be set at insert"). Every other new column below is granted.
2. `tasks_creator_not_both` mirrors `events_recorder_not_both`: a task was made by a person or by a
   process, never both. Both columns null is allowed, because every task that existed before this
   migration has no recorded creator and inventing one would be a lie.

No new CHECK holds a list of words (BUILD_PLAN rule 5): `filing_method`, `portal_kind`,
`flag_reason` and `resolution` are free text that the services check against Python tuples.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.requests
  ADD COLUMN IF NOT EXISTS assigned_to_user_id uuid REFERENCES records.users (id);
CREATE INDEX IF NOT EXISTS requests_assigned_to_idx ON records.requests (assigned_to_user_id);

ALTER TABLE records.request_items
  ADD COLUMN IF NOT EXISTS provision text;

ALTER TABLE records.tasks
  ADD COLUMN IF NOT EXISTS assigned_to_user_id uuid REFERENCES records.users (id),
  ADD COLUMN IF NOT EXISTS created_by_user_id uuid REFERENCES records.users (id),
  ADD COLUMN IF NOT EXISTS created_by_system text;
ALTER TABLE records.tasks
  ADD CONSTRAINT tasks_creator_not_both
  CHECK (created_by_user_id IS NULL OR created_by_system IS NULL);
CREATE INDEX IF NOT EXISTS tasks_assigned_open_idx
  ON records.tasks (assigned_to_user_id) WHERE completed_at IS NULL;

ALTER TABLE records.attachments
  ADD COLUMN IF NOT EXISTS page_count integer;

ALTER TABLE records.agencies
  ADD COLUMN IF NOT EXISTS short_name text,
  ADD COLUMN IF NOT EXISTS recipient_office text,
  ADD COLUMN IF NOT EXISTS mailing_street text,
  ADD COLUMN IF NOT EXISTS mailing_city_state_zip text,
  ADD COLUMN IF NOT EXISTS filing_method text,
  ADD COLUMN IF NOT EXISTS portal_kind text,
  ADD COLUMN IF NOT EXISTS request_email_verified_at timestamptz;

ALTER TABLE records.agency_contacts
  ADD COLUMN IF NOT EXISTS is_primary boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX IF NOT EXISTS agency_contacts_one_primary_idx
  ON records.agency_contacts (agency_id) WHERE is_primary;

ALTER TABLE records.sources
  ADD COLUMN IF NOT EXISTS flag_reason text,
  ADD COLUMN IF NOT EXISTS assigned_to_user_id uuid REFERENCES records.users (id),
  ADD COLUMN IF NOT EXISTS resolution text,
  ADD COLUMN IF NOT EXISTS resolution_note text;

ALTER TABLE records.fees
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS payment_method text;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT UPDATE (assigned_to_user_id) ON records.requests TO portal_app;
    GRANT UPDATE (provision) ON records.request_items TO portal_app;
    GRANT UPDATE (assigned_to_user_id) ON records.tasks TO portal_app;
    GRANT UPDATE (page_count) ON records.attachments TO portal_app;
    GRANT UPDATE (short_name, recipient_office, mailing_street, mailing_city_state_zip,
                  filing_method, portal_kind, request_email_verified_at)
      ON records.agencies TO portal_app;
    GRANT UPDATE (is_primary) ON records.agency_contacts TO portal_app;
    GRANT UPDATE (flag_reason, assigned_to_user_id, resolution, resolution_note)
      ON records.sources TO portal_app;
    GRANT UPDATE (description, payment_method) ON records.fees TO portal_app;
  END IF;
END
$$;
"""

# Dropping a column drops the indexes, the constraint and the column privileges that name it, but
# each is dropped explicitly first so the reverse reads as the exact mirror of the forward.
BACKWARD = """
ALTER TABLE records.fees
  DROP COLUMN IF EXISTS payment_method,
  DROP COLUMN IF EXISTS description;

ALTER TABLE records.sources
  DROP COLUMN IF EXISTS resolution_note,
  DROP COLUMN IF EXISTS resolution,
  DROP COLUMN IF EXISTS assigned_to_user_id,
  DROP COLUMN IF EXISTS flag_reason;

DROP INDEX IF EXISTS records.agency_contacts_one_primary_idx;
ALTER TABLE records.agency_contacts DROP COLUMN IF EXISTS is_primary;

ALTER TABLE records.agencies
  DROP COLUMN IF EXISTS request_email_verified_at,
  DROP COLUMN IF EXISTS portal_kind,
  DROP COLUMN IF EXISTS filing_method,
  DROP COLUMN IF EXISTS mailing_city_state_zip,
  DROP COLUMN IF EXISTS mailing_street,
  DROP COLUMN IF EXISTS recipient_office,
  DROP COLUMN IF EXISTS short_name;

ALTER TABLE records.attachments DROP COLUMN IF EXISTS page_count;

DROP INDEX IF EXISTS records.tasks_assigned_open_idx;
ALTER TABLE records.tasks DROP CONSTRAINT IF EXISTS tasks_creator_not_both;
ALTER TABLE records.tasks
  DROP COLUMN IF EXISTS created_by_system,
  DROP COLUMN IF EXISTS created_by_user_id,
  DROP COLUMN IF EXISTS assigned_to_user_id;

ALTER TABLE records.request_items DROP COLUMN IF EXISTS provision;

DROP INDEX IF EXISTS records.requests_assigned_to_idx;
ALTER TABLE records.requests DROP COLUMN IF EXISTS assigned_to_user_id;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0005_request_item_sort_order")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
