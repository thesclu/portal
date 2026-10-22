"""Agency operations (BUILD_PLAN P16). One new table and four columns.

  records.agency_closures  whole days an agency's office is closed, as a dated range with the
                           reason and the citation a person typed (DECISIONS 21: the legal basis
                           of a closure is owner's to enter; nothing here fills it). A closure is
                           one more holiday on that agency's calendar when a clock is computed.
                           Never deleted; voided with a note.

  agencies                 gain `merged_into_agency_id`, `merged_at`, `merged_by_user_id` and
                           `merge_note`: the duplicate is marked as the same as the one kept and
                           nothing is rewritten (DECISIONS 2). Resolved when read; undone by
                           clearing the four columns.

Grants: SELECT, INSERT and UPDATE of the void columns on the new table; UPDATE of the four merge
columns on agencies. Never DELETE. The reverse fails loudly while any closure exists or any agency
is merged; the placement pin becomes 26.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.agencies
  ADD COLUMN merged_into_agency_id uuid REFERENCES records.agencies (id),
  ADD COLUMN merged_at timestamptz,
  ADD COLUMN merged_by_user_id uuid REFERENCES records.users (id),
  ADD COLUMN merge_note text,
  ADD CONSTRAINT agencies_merge_together
    CHECK ((merged_into_agency_id IS NULL) = (merged_at IS NULL)),
  ADD CONSTRAINT agencies_not_merged_into_self
    CHECK (merged_into_agency_id IS NULL OR merged_into_agency_id <> id);

CREATE TABLE records.agency_closures (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agency_id           uuid NOT NULL REFERENCES records.agencies (id),
  starts_on           date NOT NULL,
  ends_on             date NOT NULL,
  reason              text NOT NULL,
  citation            text NOT NULL,
  created_by_user_id  uuid NOT NULL REFERENCES records.users (id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  voided_at           timestamptz,
  voided_by_user_id   uuid REFERENCES records.users (id),
  void_note           text,
  CONSTRAINT agency_closures_range CHECK (ends_on >= starts_on),
  CONSTRAINT agency_closures_reason_present CHECK (btrim(reason) <> ''),
  CONSTRAINT agency_closures_citation_present CHECK (btrim(citation) <> ''),
  CONSTRAINT agency_closures_void_together
    CHECK ((voided_at IS NULL) = (voided_by_user_id IS NULL))
);
CREATE INDEX agency_closures_agency_idx ON records.agency_closures (agency_id, starts_on);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.agency_closures TO portal_app;
    GRANT UPDATE (voided_at, voided_by_user_id, void_note)
      ON records.agency_closures TO portal_app;
    GRANT UPDATE (merged_into_agency_id, merged_at, merged_by_user_id, merge_note)
      ON records.agencies TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.agency_closures;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0016: % closure(s) exist. Ledger rows are never deleted to '
      'make a reverse succeed.', blocked;
  END IF;
  SELECT count(*) INTO blocked FROM records.agencies WHERE merged_into_agency_id IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0016: % agenc(ies) are merged; unmerge them first.', blocked;
  END IF;
END
$$;
DROP TABLE records.agency_closures;
ALTER TABLE records.agencies
  DROP CONSTRAINT agencies_merge_together,
  DROP CONSTRAINT agencies_not_merged_into_self,
  DROP COLUMN merged_into_agency_id,
  DROP COLUMN merged_at,
  DROP COLUMN merged_by_user_id,
  DROP COLUMN merge_note;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0015_challenges")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
