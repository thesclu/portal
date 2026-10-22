"""What design v2 and the two decisions the P3b gate left open need from the drafting schema.

`db/drafting/drafting-schema.sql` is applied by 0001 and sha-pinned; it is not edited. This is the
first migration on top of it (BUILD_PLAN P10; DECISIONS, "Design v2").

1. **A recipient may hold a reserved request while still `pending`.** The design reserves a
   tracking number and an alias before anything is sent, because the cover message goes out from
   the alias and a web form needs it as its contact address. `letter_recipients_filed_has_request`
   said "filed if and only if it has a request"; it now says only "a filed recipient has a
   request". `letter_recipients_filed_has_time` is unchanged: `filed_at` still means the letter
   went out. The two delete guards already key on `request_id`, so a reserved recipient is already
   undeletable, which is right: its request exists in `records` for good.

2. **A revision says who saved it and what changed.** `letter_template_revisions` gains
   `created_by_user_id` and `summary`. A revision is now minted when a template is SAVED and when
   a letter is STARTED from one, no longer when a letter is filed.

3. **A draft records the revision it was started from**: `letter_drafts.source_revision_id`. The
   recipient's `template_revision_id` is copied from it at reservation, so a filed letter points
   at the template exactly as it stood when the letter was started.

4. **An item can name the provision it has its own clock under**: `provision` on
   `letter_template_items` and `letter_draft_items`, carried to `records.request_items.provision`
   at reservation. It is a label the rules package is asked about; no number lives here.

Grants: every table but `letter_template_revisions` already has table-level SELECT, INSERT, UPDATE
and DELETE, and revisions have table-level SELECT and INSERT, so the new columns are covered and a
revision's author and summary are as write-once as the rest of the row.

The reverse restores the two-way constraint and drops the columns, and FAILS LOUDLY, naming what
blocks it, while any row uses what it would remove: a reserved recipient that is not filed, a
revision with an author or a summary, a draft that records its revision, an item with a provision.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE drafting.letter_recipients
  DROP CONSTRAINT letter_recipients_filed_has_request,
  ADD CONSTRAINT letter_recipients_filed_has_request
    CHECK (status <> 'filed' OR request_id IS NOT NULL);

ALTER TABLE drafting.letter_template_revisions
  ADD COLUMN created_by_user_id uuid REFERENCES records.users (id),
  ADD COLUMN summary text;

ALTER TABLE drafting.letter_drafts
  ADD COLUMN source_revision_id uuid REFERENCES drafting.letter_template_revisions (id);

ALTER TABLE drafting.letter_template_items ADD COLUMN provision text;
ALTER TABLE drafting.letter_draft_items ADD COLUMN provision text;
"""

BACKWARD = """
DO $$
DECLARE
  reserved bigint;
  authored bigint;
  pointed bigint;
  labelled bigint;
BEGIN
  SELECT count(*) INTO reserved FROM drafting.letter_recipients
    WHERE request_id IS NOT NULL AND status <> 'filed';
  SELECT count(*) INTO authored FROM drafting.letter_template_revisions
    WHERE created_by_user_id IS NOT NULL OR summary IS NOT NULL;
  SELECT count(*) INTO pointed FROM drafting.letter_drafts WHERE source_revision_id IS NOT NULL;
  SELECT (SELECT count(*) FROM drafting.letter_template_items WHERE provision IS NOT NULL)
       + (SELECT count(*) FROM drafting.letter_draft_items WHERE provision IS NOT NULL)
    INTO labelled;
  IF reserved + authored + pointed + labelled > 0 THEN
    RAISE EXCEPTION
      'cannot reverse drafting_db.0002: % reserved recipient(s) not yet filed, % revision(s) with '
      'an author or summary, % draft(s) recording their revision, % item(s) with a provision. '
      'Nothing is rewritten to make a reverse succeed.',
      reserved, authored, pointed, labelled;
  END IF;
END
$$;

ALTER TABLE drafting.letter_draft_items DROP COLUMN provision;
ALTER TABLE drafting.letter_template_items DROP COLUMN provision;
ALTER TABLE drafting.letter_drafts DROP COLUMN source_revision_id;
ALTER TABLE drafting.letter_template_revisions
  DROP COLUMN summary,
  DROP COLUMN created_by_user_id;
ALTER TABLE drafting.letter_recipients
  DROP CONSTRAINT letter_recipients_filed_has_request,
  ADD CONSTRAINT letter_recipients_filed_has_request
    CHECK ((status = 'filed') = (request_id IS NOT NULL));
"""


class Migration(migrations.Migration):
    dependencies = [
        ("drafting_db", "0001_drafting_schema_sql"),
        # records.users, which revisions now point at, exists from records_db.0001; the dependency
        # that matters is only that this runs after the schema it alters.
    ]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
