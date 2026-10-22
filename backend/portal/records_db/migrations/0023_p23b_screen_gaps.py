"""P23b: what the P20 screens drew and the ledger could not hold. One table and two columns.

  records.task_notes   notes on a task (177 "Write a note…"), the message-notes pattern of
                       records_db.0009: who wrote it, the text, when; voided with a reason and by
                       whom, never deleted, and the text is not editable. Row-level security
                       borrows the task's own visibility (the policy reads `records.tasks`, whose
                       policy is the request's), as production_review_items borrows its review's.

  tasks.source_id      the message a task is about (a bounce's sent message for "Retry send", the
                       message a classification opened the task from, a task made from a message).
                       Written once, when the task is made: no UPDATE grant.

  agencies.imported_from
                       the file an agency was created from by the importer (172 "added by
                       import"); NULL for an agency added by hand. Written once: no UPDATE grant.

Grants: SELECT and INSERT on the new table, UPDATE of its three void columns only; never DELETE.
The two columns are covered by their tables' existing SELECT and INSERT grants (0002), and are
named here again so the migration reads whole. The reverse fails loudly while any note exists or
either column holds a value: ledger rows are never deleted to make a reverse succeed. The records
placement pin becomes 28 and the row-level security pin gains `task_notes` in the same commit.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE records.task_notes (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  task_id            uuid NOT NULL REFERENCES records.tasks (id),
  author_user_id     uuid NOT NULL REFERENCES records.users (id),
  body               text NOT NULL,
  voided_at          timestamptz,
  voided_by_user_id  uuid REFERENCES records.users (id),
  void_reason        text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT task_notes_body_present CHECK (btrim(body) <> ''),
  CONSTRAINT task_notes_void_together
    CHECK ((voided_at IS NULL) = (voided_by_user_id IS NULL))
);
CREATE INDEX task_notes_task_id_created_at_idx ON records.task_notes (task_id, created_at);

ALTER TABLE records.tasks ADD COLUMN source_id uuid REFERENCES records.sources (id);
CREATE INDEX tasks_source_id_idx ON records.tasks (source_id) WHERE source_id IS NOT NULL;

ALTER TABLE records.agencies ADD COLUMN imported_from text;

ALTER TABLE records.task_notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_notes_read ON records.task_notes FOR SELECT TO portal_app
  USING (EXISTS (SELECT 1 FROM records.tasks t WHERE t.id = task_id));
CREATE POLICY task_notes_update ON records.task_notes FOR UPDATE TO portal_app
  USING (EXISTS (SELECT 1 FROM records.tasks t WHERE t.id = task_id)) WITH CHECK (true);
CREATE POLICY task_notes_insert ON records.task_notes FOR INSERT TO portal_app
  WITH CHECK (records.app_context() IN ('web', 'system'));

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.task_notes TO portal_app;
    GRANT UPDATE (voided_at, voided_by_user_id, void_reason) ON records.task_notes TO portal_app;
    GRANT SELECT (source_id), INSERT (source_id) ON records.tasks TO portal_app;
    GRANT SELECT (imported_from), INSERT (imported_from) ON records.agencies TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.task_notes;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0023: % note(s) on tasks exist. Ledger rows are never deleted '
      'to make a reverse succeed.', blocked;
  END IF;
  SELECT count(*) INTO blocked FROM records.tasks WHERE source_id IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0023: % task(s) name the message they are about.', blocked;
  END IF;
  SELECT count(*) INTO blocked FROM records.agencies WHERE imported_from IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0023: % agenc(ies) record the import they came from.', blocked;
  END IF;
END
$$;

DROP POLICY IF EXISTS task_notes_read ON records.task_notes;
DROP POLICY IF EXISTS task_notes_update ON records.task_notes;
DROP POLICY IF EXISTS task_notes_insert ON records.task_notes;
DROP TABLE records.task_notes;
ALTER TABLE records.agencies DROP COLUMN imported_from;
DROP INDEX IF EXISTS records.tasks_source_id_idx;
ALTER TABLE records.tasks DROP COLUMN source_id;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0022_status_waiting_for_approval")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
