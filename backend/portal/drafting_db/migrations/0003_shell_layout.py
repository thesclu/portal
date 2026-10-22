"""The letter as a PDF needs a little more than text (BUILD_PLAN P11): who signs it, and the page
furniture. Four nullable columns on `letter_shells`; everything else about the layout is the
renderer's. The seal (an image) is not here: it is a stored file, and nothing stores one yet."""

from django.db import migrations

FORWARD = """
ALTER TABLE drafting.letter_shells
  ADD COLUMN IF NOT EXISTS signer_name  text,
  ADD COLUMN IF NOT EXISTS signer_title text,
  ADD COLUMN IF NOT EXISTS font_size_pt integer,
  ADD COLUMN IF NOT EXISTS margin_mm    integer;
"""

BACKWARD = """
ALTER TABLE drafting.letter_shells
  DROP COLUMN IF EXISTS signer_name,
  DROP COLUMN IF EXISTS signer_title,
  DROP COLUMN IF EXISTS font_size_pt,
  DROP COLUMN IF EXISTS margin_mm;
"""


class Migration(migrations.Migration):
    dependencies = [("drafting_db", "0002_v2")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
