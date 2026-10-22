"""records.request_items.sort_order: the order the items appear in the letter.

`item_number` is free text ('3', '4a', '10'), so ordering by it sorts as a string
and puts '10' before '2'. Items are numbered in a legal document and agencies
answer them one by one, so their order is correctness, not presentation, and
letters already routinely carry seven or more.

The alternative -- leave the order on the drafting side and have the request
screen read it from there -- is the one thing the architecture forbids: every
foreign key runs drafting -> records and nothing points back. Paying for a
column is cheaper than reversing that arrow.

It is also cheapest now. Today this is an empty column on a table nothing
bulk-writes to. Once drafting is live, adding it means backfilling an order for
existing rows from a source that no longer reliably holds it.

Additive; db/records/schema.sql is untouched and its sha256 is unaffected.
Shaped after 0003_additive, including the column-level GRANT.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.request_items
  ADD COLUMN IF NOT EXISTS sort_order int NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS request_items_sort_idx
  ON records.request_items (request_id, sort_order);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT UPDATE (sort_order) ON records.request_items TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DROP INDEX IF EXISTS records.request_items_sort_idx;
ALTER TABLE records.request_items DROP COLUMN IF EXISTS sort_order;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0004_status_no_records_claimed")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
