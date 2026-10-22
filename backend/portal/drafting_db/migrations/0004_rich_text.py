"""Rich text, prose and a letterhead's layout (BUILD_PLAN P19b, DECISIONS 25). Additive only:

  letter_template_items, letter_draft_items
      `kind` ('item' | 'prose'): prose is a paragraph that belongs to the letter and sits in the
      same order as the items, numbered by nothing; `body_rich`: the editor's document (JSON)
      beside the plain `body`, which stays what the request records and what search reads.
  shared_blocks.body_rich, letter_shells.opening_rich / closing_rich: the same, for prose that
      is reused.
  letter_shells: `font`, `margin_inches`, `seal_position`, `seal_file`: what the page looks like,
      for the PDF and the screen alike. The seal is a stored file named here.

DELETE on drafting tables is already granted (a draft is scratch until filed); the new columns
inherit the tables' grants.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE drafting.letter_template_items
  ADD COLUMN IF NOT EXISTS kind      text NOT NULL DEFAULT 'item',
  ADD COLUMN IF NOT EXISTS body_rich jsonb;
ALTER TABLE drafting.letter_template_items
  ADD CONSTRAINT letter_template_items_kind_check CHECK (kind IN ('item', 'prose'));
ALTER TABLE drafting.letter_draft_items
  ADD COLUMN IF NOT EXISTS kind      text NOT NULL DEFAULT 'item',
  ADD COLUMN IF NOT EXISTS body_rich jsonb;
ALTER TABLE drafting.letter_draft_items
  ADD CONSTRAINT letter_draft_items_kind_check CHECK (kind IN ('item', 'prose'));
ALTER TABLE drafting.shared_blocks
  ADD COLUMN IF NOT EXISTS body_rich jsonb;
ALTER TABLE drafting.letter_shells
  ADD COLUMN IF NOT EXISTS opening_rich  jsonb,
  ADD COLUMN IF NOT EXISTS closing_rich  jsonb,
  ADD COLUMN IF NOT EXISTS font          text,
  ADD COLUMN IF NOT EXISTS margin_inches numeric(4, 2),
  ADD COLUMN IF NOT EXISTS seal_position text,
  ADD COLUMN IF NOT EXISTS seal_file     text;
ALTER TABLE drafting.letter_shells
  ADD CONSTRAINT letter_shells_seal_position_check
  CHECK (seal_position IS NULL OR seal_position IN ('left', 'center', 'none'));
"""

BACKWARD = """
ALTER TABLE drafting.letter_shells
  DROP CONSTRAINT IF EXISTS letter_shells_seal_position_check,
  DROP COLUMN IF EXISTS opening_rich,
  DROP COLUMN IF EXISTS closing_rich,
  DROP COLUMN IF EXISTS font,
  DROP COLUMN IF EXISTS margin_inches,
  DROP COLUMN IF EXISTS seal_position,
  DROP COLUMN IF EXISTS seal_file;
ALTER TABLE drafting.shared_blocks DROP COLUMN IF EXISTS body_rich;
ALTER TABLE drafting.letter_draft_items
  DROP CONSTRAINT IF EXISTS letter_draft_items_kind_check,
  DROP COLUMN IF EXISTS kind,
  DROP COLUMN IF EXISTS body_rich;
ALTER TABLE drafting.letter_template_items
  DROP CONSTRAINT IF EXISTS letter_template_items_kind_check,
  DROP COLUMN IF EXISTS kind,
  DROP COLUMN IF EXISTS body_rich;
"""


class Migration(migrations.Migration):
    dependencies = [("drafting_db", "0003_shell_layout")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
