"""Deadline kinds become a lookup table, and `events.channel` admits `in_person`.

THIS IS THE ONE MIGRATION THAT IS NOT PURELY ADDITIVE (DECISIONS, "Design v2"; HANDOFF convention
11). It replaces three CHECK constraints that schema.sql defines. owner delegated the call. It is
alone in its file so it can be reviewed and reversed alone. schema.sql is untouched and its sha256
does not move.

What it does, and why each piece:

1. `records.deadline_definitions`: the kinds of deadline, as rows. The two-value CHECK on
   `deadlines.deadline_type` could not hold "Fee response" or "Other", and widening a CHECK was the
   only non-additive thing this schema ever needed, so the list becomes data. `overdue_status` says
   which status a lapsed deadline of that kind moves the request to; null means lapsing moves no
   status (a fee response that goes unanswered is not a statutory event). It replaces the
   `OVERDUE_STATUS` dict in services/deadlines.py.
2. The CHECK on `deadlines.deadline_type` and on `deadline_followups.deadline_type` is dropped and
   replaced by a foreign key to the new table. Every existing row holds one of the two seeded codes,
   so the foreign key validates.
3. The CHECK on `events.channel` is replaced by one that also admits `in_person` ("Meeting or
   visit"). It keeps the name Postgres gave the original, `events_channel_check`.

The three constraint names below are the ones Postgres generated when schema.sql created the
tables, read from `pg_constraint` on 2026-10-03, not assumed:

    events             events_channel_check
    deadlines          deadlines_deadline_type_check
    deadline_followups deadline_followups_deadline_type_check

They are dropped with NO `IF EXISTS`. If a database has them under other names, this fails loudly
instead of leaving a CHECK and a foreign key both standing.

Grants on the new table (0002's `ON ALL TABLES` covered only the tables that existed then):
SELECT, INSERT, and UPDATE on (label, sort_order). Never DELETE, and not `code` or
`overdue_status`: the status a lapse moves a request to is a decision, not an edit.

The reverse restores the three CHECKs. It FAILS LOUDLY, by an explicit exception that names what
blocks it, if any deadline or follow-up of a kind the old CHECK did not allow, or any event with
channel `in_person`, exists. Silently rewriting or deleting ledger rows to make a reverse succeed
is the one thing this system never does.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE records.deadline_definitions (
  code            text PRIMARY KEY,
  label           text NOT NULL,
  sort_order      int  NOT NULL,
  overdue_status  text REFERENCES records.status_definitions (code)  -- null: lapsing moves no status
);

INSERT INTO records.deadline_definitions (code, label, sort_order, overdue_status) VALUES
  ('statutory_determination', 'Determination', 10, 'determination_overdue'),
  ('production',              'Production',    20, 'production_overdue'),
  ('fee_response',            'Fee response',  30, NULL),
  ('other',                   'Other',         40, NULL);

ALTER TABLE records.deadlines DROP CONSTRAINT deadlines_deadline_type_check;
ALTER TABLE records.deadlines
  ADD CONSTRAINT deadlines_deadline_type_fkey
  FOREIGN KEY (deadline_type) REFERENCES records.deadline_definitions (code);

ALTER TABLE records.deadline_followups DROP CONSTRAINT deadline_followups_deadline_type_check;
ALTER TABLE records.deadline_followups
  ADD CONSTRAINT deadline_followups_deadline_type_fkey
  FOREIGN KEY (deadline_type) REFERENCES records.deadline_definitions (code);

ALTER TABLE records.events DROP CONSTRAINT events_channel_check;
ALTER TABLE records.events
  ADD CONSTRAINT events_channel_check
  CHECK (channel IN ('email', 'portal', 'mail', 'call', 'system', 'in_person'));

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.deadline_definitions TO portal_app;
    GRANT UPDATE (label, sort_order) ON records.deadline_definitions TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked_deadlines  bigint;
  blocked_followups  bigint;
  blocked_events     bigint;
BEGIN
  SELECT count(*) INTO blocked_deadlines FROM records.deadlines
    WHERE deadline_type NOT IN ('statutory_determination', 'production');
  SELECT count(*) INTO blocked_followups FROM records.deadline_followups
    WHERE deadline_type NOT IN ('statutory_determination', 'production');
  SELECT count(*) INTO blocked_events FROM records.events WHERE channel = 'in_person';
  IF blocked_deadlines > 0 OR blocked_followups > 0 OR blocked_events > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0007: % deadline row(s) and % follow-up row(s) of a kind the '
      'original CHECK does not allow, and % event(s) on channel in_person, exist. Ledger rows '
      'are never rewritten or deleted to make a reverse succeed.',
      blocked_deadlines, blocked_followups, blocked_events;
  END IF;
END
$$;

ALTER TABLE records.events DROP CONSTRAINT events_channel_check;
ALTER TABLE records.events
  ADD CONSTRAINT events_channel_check
  CHECK (channel IN ('email', 'portal', 'mail', 'call', 'system'));

ALTER TABLE records.deadline_followups DROP CONSTRAINT deadline_followups_deadline_type_fkey;
ALTER TABLE records.deadline_followups
  ADD CONSTRAINT deadline_followups_deadline_type_check
  CHECK (deadline_type IN ('statutory_determination', 'production'));

ALTER TABLE records.deadlines DROP CONSTRAINT deadlines_deadline_type_fkey;
ALTER TABLE records.deadlines
  ADD CONSTRAINT deadlines_deadline_type_check
  CHECK (deadline_type IN ('statutory_determination', 'production'));

DROP TABLE records.deadline_definitions;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0006_v2_columns")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
