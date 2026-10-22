"""Two statuses and two event types the design's classification menus need.

records-schema.md: adding a status or an event type is an insert, not a schema change. It is done as
a data migration only so every environment has the rows, shaped after 0004.

Statuses (neither terminal; both are states a request waits in, not ends):
  clarification_requested  'Clarification requested'  25  the agency asked what we meant
  fee_estimate_received    'Fee estimate received'    36  the agency named a price, nobody has
                                                          decided yet

Event types:
  fee_payment  'Fee payment'  39  money moved; the fee row carries the amount
  narrowing    'Narrowing'    72  we and the agency agreed to ask for less

The sort orders slot each row between the neighbours the design's lists put it between.
"""

from django.db import migrations

INSERT = """
INSERT INTO records.status_definitions (code, label, sort_order, is_terminal) VALUES
  ('clarification_requested', 'Clarification requested', 25, false),
  ('fee_estimate_received',   'Fee estimate received',    36, false)
ON CONFLICT (code) DO NOTHING;

INSERT INTO records.event_definitions (code, label, sort_order) VALUES
  ('fee_payment', 'Fee payment', 39),
  ('narrowing',   'Narrowing',   72)
ON CONFLICT (code) DO NOTHING;
"""

# A reverse that deletes a row some ledger row already points at fails on the foreign key, which
# is the right answer: the rows are history by then.
DELETE = """
DELETE FROM records.event_definitions WHERE code IN ('fee_payment', 'narrowing');
DELETE FROM records.status_definitions
  WHERE code IN ('clarification_requested', 'fee_estimate_received');
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0007_deadline_kinds_and_channel")]
    operations = [migrations.RunSQL(sql=[INSERT], reverse_sql=[DELETE])]
