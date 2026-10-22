"""A block's own footnotes and a letterhead's own blocks (BUILD_PLAN P19h). Additive only:

  shared_block_citations: a shared block's footnotes, kept with the block so every template
      that pulls the block in cites the same thing. When a letter is assembled they are
      renumbered after the template's own citations (`services/common.resolve_blocks`).
  letter_shell_blocks: the shared blocks a letterhead adds to every letter started on it. A
      template created on the letterhead copies them into its own Parts list; a letter written
      from scratch on it copies them as prose. Nothing reads this table at render time.

Grants follow the schema's rule: drafting tables get SELECT, INSERT, UPDATE and DELETE.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE IF NOT EXISTS drafting.shared_block_citations (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  shared_block_id  uuid NOT NULL REFERENCES drafting.shared_blocks(id) ON DELETE CASCADE,
  marker           text NOT NULL,
  citation_text    text NOT NULL,
  sort_order       int  NOT NULL DEFAULT 0,
  UNIQUE (shared_block_id, marker)
);
CREATE INDEX IF NOT EXISTS shared_block_citations_block_idx
  ON drafting.shared_block_citations (shared_block_id, sort_order);
CREATE TABLE IF NOT EXISTS drafting.letter_shell_blocks (
  shell_id         uuid NOT NULL REFERENCES drafting.letter_shells(id) ON DELETE CASCADE,
  shared_block_id  uuid NOT NULL REFERENCES drafting.shared_blocks(id),
  sort_order       int  NOT NULL DEFAULT 0,
  PRIMARY KEY (shell_id, shared_block_id)
);
GRANT SELECT, INSERT, UPDATE, DELETE
  ON drafting.shared_block_citations, drafting.letter_shell_blocks TO portal_app;
"""

BACKWARD = """
DROP TABLE IF EXISTS drafting.letter_shell_blocks;
DROP TABLE IF EXISTS drafting.shared_block_citations;
"""


class Migration(migrations.Migration):
    dependencies = [("drafting_db", "0004_rich_text")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
