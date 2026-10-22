"""A status for an intern's letter waiting for an approver (P20h; DECISIONS 42c).

records-schema.md: adding a status is an insert, not a migration. It is done as a data migration
only so every environment has the row, as 0004 did for `no_records_claimed`. Shown as "Filing ·
waiting for approval" (`frontend/src/lib/status.ts`). Not terminal, and between `pending_filing`
(5) and `filed` (10) in the order: the letter is reserved and not yet out.

It is written only by `mail/services/approval.py` (submitting, and the way back on withdraw or
send back), through the status ledger like every other status, never into
`requests.current_status`. Nothing automated writes it.
"""

from django.db import migrations

INSERT = """
INSERT INTO records.status_definitions (code, label, sort_order, is_terminal)
VALUES ('waiting_for_approval', 'Waiting for approval', 7, false)
ON CONFLICT (code) DO NOTHING;
"""

DELETE = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM records.status_changes WHERE status = 'waiting_for_approval') THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0022: status rows of waiting_for_approval exist; the ledger is '
      'never rewritten to make a reverse succeed.';
  END IF;
END
$$;
DELETE FROM records.status_definitions WHERE code = 'waiting_for_approval';
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0021_role_visibility")]
    operations = [migrations.RunSQL(sql=[INSERT], reverse_sql=[DELETE])]
