"""Appeals and search challenges (BUILD_PLAN P14). One new table, two vocabulary rows, and one
widened CHECK.

  records.challenges   one appeal of a withholding, or one challenge to a "no records" search,
                       scoped to a request item or a production-review row when it is about one.
                       `basis` is the person's own words for why. `message_id` names the letter
                       in the mail schema as a plain uuid, NOT a foreign key: records never points
                       at mail (the dependency runs the other way, and mail reports back through
                       `services.challenges.on_message_sent`). `sent_at` and `event_id` are set
                       when the letter goes; `followup_deadline_id` is the date to follow up by;
                       the outcome is recorded by a person. Never deleted.

  deadline_definitions gains `challenge_response` (lapsing moves no status: the request stays
                       `appealed` or whatever it is until a person decides).
  event_definitions    gains `challenge` (a search challenge); `appeal` already exists.

  deadlines.basis      admits `internal`: a date we set for ourselves, which is what "follow up
                       by" is. The CHECK is replaced the way 0007 and 0013 replaced theirs: the
                       name Postgres gave the original, read from pg_constraint on 2026-10-06,
                       dropped with no IF EXISTS.

Grants: SELECT, INSERT, and UPDATE on the columns a later step fills; never DELETE. The reverse
fails loudly while any challenge exists; the placement pin becomes 25.
"""

from django.db import migrations

FORWARD = """
INSERT INTO records.deadline_definitions (code, label, sort_order, overdue_status)
  VALUES ('challenge_response', 'Challenge response', 35, NULL)
  ON CONFLICT (code) DO NOTHING;
INSERT INTO records.event_definitions (code, label, sort_order)
  VALUES ('challenge', 'Challenge', 85)
  ON CONFLICT (code) DO NOTHING;

ALTER TABLE records.deadlines DROP CONSTRAINT deadlines_basis_check;
ALTER TABLE records.deadlines
  ADD CONSTRAINT deadlines_basis_check
  CHECK (basis IN ('statutory', 'agency_commitment', 'internal'));

CREATE TABLE records.challenges (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id            uuid NOT NULL REFERENCES records.requests (id),
  kind                  text NOT NULL CHECK (kind IN ('appeal', 'search_challenge')),
  request_item_id       uuid REFERENCES records.request_items (id),
  review_item_id        uuid REFERENCES records.production_review_items (id),
  basis                 text NOT NULL,
  follow_up_by          date,
  message_id            uuid,
  created_by_user_id    uuid NOT NULL REFERENCES records.users (id),
  created_at            timestamptz NOT NULL DEFAULT now(),
  sent_at               timestamptz,
  event_id              uuid REFERENCES records.events (id),
  followup_deadline_id  uuid REFERENCES records.deadlines (id),
  outcome               text CHECK (outcome IN ('reversed', 'partly_reversed', 'upheld', 'withdrawn')),
  outcome_note          text,
  resolved_at           timestamptz,
  resolved_by_user_id   uuid REFERENCES records.users (id),
  CONSTRAINT challenges_resolved_together CHECK ((resolved_at IS NULL) = (outcome IS NULL))
);
CREATE INDEX challenges_request_idx ON records.challenges (request_id, created_at);
CREATE UNIQUE INDEX challenges_message_uniq
  ON records.challenges (message_id) WHERE message_id IS NOT NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.challenges TO portal_app;
    GRANT UPDATE (follow_up_by, message_id, sent_at, event_id, followup_deadline_id, outcome,
                  outcome_note, resolved_at, resolved_by_user_id)
      ON records.challenges TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.challenges;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0015: % challenge(s) exist. Ledger rows are never deleted to '
      'make a reverse succeed.', blocked;
  END IF;
  SELECT count(*) INTO blocked FROM records.deadlines WHERE basis = 'internal';
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0015: % deadline(s) have basis internal and would violate the '
      'original CHECK.', blocked;
  END IF;
END
$$;
DROP TABLE records.challenges;
ALTER TABLE records.deadlines DROP CONSTRAINT deadlines_basis_check;
ALTER TABLE records.deadlines
  ADD CONSTRAINT deadlines_basis_check CHECK (basis IN ('statutory', 'agency_commitment'));
DELETE FROM records.deadline_definitions WHERE code = 'challenge_response'
  AND NOT EXISTS (SELECT 1 FROM records.deadlines WHERE deadline_type = 'challenge_response');
DELETE FROM records.event_definitions WHERE code = 'challenge'
  AND NOT EXISTS (SELECT 1 FROM records.events WHERE event_type = 'challenge');
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0014_access_requests")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
