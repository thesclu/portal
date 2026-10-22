"""The drafting tool's ids, so the registry can join the fork's documents (BUILD_PLAN P21c;
DECISIONS 49). Additive only; `drafting-schema.sql` stays as pinned.

  letter_drafts       fork_wave_id, fork_folder_id: the wave (one per draft, keyed in the fork by
                      the draft's id) and its folder; fork_master_document_id,
                      fork_master_version_id, fork_master_version_number: the master letter and
                      the version the portal last heard of. A wave and its master are set
                      together or not at all.
  letter_recipients   fork_document_id, fork_version_id, fork_version_number: this agency's
                      letter, made by the fork's merge at reserve; fork_text_sha256: the SHA-256
                      of the .docx the reserved request's words were last written from, so filing
                      knows whether the letter changed since.
  letter_templates    fork_template_id: the organisation template in the fork this row mirrors
                      (the Templates pages tell the portal; `sclu-templates/1`).
  fork_events         every signed webhook the fork delivered, by its event id: the store that
                      makes a delivery count once (the fork delivers at least once and retries
                      with the same id), and what the Files tab and the Suggestions badge read.
                      Insert-only: SELECT and INSERT, never UPDATE or DELETE.

Each id is unique where set: one draft per wave and per master, one recipient per agency letter,
one template per fork template. The new columns inherit their tables' grants (0001: SELECT,
INSERT, UPDATE, DELETE). The reverse refuses while anything is joined or any event is stored:
nothing is deleted to make a reverse succeed.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE drafting.letter_drafts
  ADD COLUMN fork_wave_id               uuid,
  ADD COLUMN fork_folder_id             uuid,
  ADD COLUMN fork_master_document_id    uuid,
  ADD COLUMN fork_master_version_id     uuid,
  ADD COLUMN fork_master_version_number int;
ALTER TABLE drafting.letter_drafts
  ADD CONSTRAINT letter_drafts_fork_wave_id_key UNIQUE (fork_wave_id),
  ADD CONSTRAINT letter_drafts_fork_master_document_id_key UNIQUE (fork_master_document_id),
  ADD CONSTRAINT letter_drafts_fork_wave_whole
    CHECK ((fork_wave_id IS NULL) = (fork_master_document_id IS NULL));

ALTER TABLE drafting.letter_recipients
  ADD COLUMN fork_document_id    uuid,
  ADD COLUMN fork_version_id     uuid,
  ADD COLUMN fork_version_number int,
  ADD COLUMN fork_text_sha256    text;
ALTER TABLE drafting.letter_recipients
  ADD CONSTRAINT letter_recipients_fork_document_id_key UNIQUE (fork_document_id);

ALTER TABLE drafting.letter_templates
  ADD COLUMN fork_template_id uuid;
ALTER TABLE drafting.letter_templates
  ADD CONSTRAINT letter_templates_fork_template_id_key UNIQUE (fork_template_id);

CREATE TABLE drafting.fork_events (
  id           text PRIMARY KEY,
  event_type   text NOT NULL,
  document_id  uuid,
  occurred_at  timestamptz,
  received_at  timestamptz NOT NULL DEFAULT now(),
  data         jsonb NOT NULL DEFAULT '{}'::jsonb,
  CONSTRAINT fork_events_id_length CHECK (char_length(id) BETWEEN 1 AND 200)
);
CREATE INDEX fork_events_document_idx
  ON drafting.fork_events (document_id, event_type, occurred_at);

GRANT SELECT, INSERT ON drafting.fork_events TO portal_app;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM drafting.letter_drafts
    WHERE fork_wave_id IS NOT NULL OR fork_folder_id IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION 'cannot reverse drafting_db.0006: % draft(s) are joined to the drafting tool.',
      blocked;
  END IF;
  SELECT count(*) INTO blocked FROM drafting.letter_recipients WHERE fork_document_id IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse drafting_db.0006: % recipient(s) name their letter in the drafting tool.',
      blocked;
  END IF;
  SELECT count(*) INTO blocked FROM drafting.letter_templates WHERE fork_template_id IS NOT NULL;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse drafting_db.0006: % template(s) mirror one in the drafting tool.', blocked;
  END IF;
  IF to_regclass('drafting.fork_events') IS NOT NULL THEN
    SELECT count(*) INTO blocked FROM drafting.fork_events;
    IF blocked > 0 THEN
      RAISE EXCEPTION 'cannot reverse drafting_db.0006: % webhook event(s) are stored.', blocked;
    END IF;
  END IF;
END $$;
DROP TABLE IF EXISTS drafting.fork_events;
ALTER TABLE drafting.letter_templates
  DROP CONSTRAINT IF EXISTS letter_templates_fork_template_id_key,
  DROP COLUMN IF EXISTS fork_template_id;
ALTER TABLE drafting.letter_recipients
  DROP CONSTRAINT IF EXISTS letter_recipients_fork_document_id_key,
  DROP COLUMN IF EXISTS fork_document_id,
  DROP COLUMN IF EXISTS fork_version_id,
  DROP COLUMN IF EXISTS fork_version_number,
  DROP COLUMN IF EXISTS fork_text_sha256;
ALTER TABLE drafting.letter_drafts
  DROP CONSTRAINT IF EXISTS letter_drafts_fork_wave_whole,
  DROP CONSTRAINT IF EXISTS letter_drafts_fork_master_document_id_key,
  DROP CONSTRAINT IF EXISTS letter_drafts_fork_wave_id_key,
  DROP COLUMN IF EXISTS fork_wave_id,
  DROP COLUMN IF EXISTS fork_folder_id,
  DROP COLUMN IF EXISTS fork_master_document_id,
  DROP COLUMN IF EXISTS fork_master_version_id,
  DROP COLUMN IF EXISTS fork_master_version_number;
"""


class Migration(migrations.Migration):
    dependencies = [("drafting_db", "0005_block_citations_and_shell_blocks")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
