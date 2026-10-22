"""Asking for access to a privileged request (BUILD_PLAN P13). A new table; additive.

A person who cannot see a privileged request may ask for it, saying why. Someone who can see it
allows or declines, and allowing writes the P9 grant (`request_access`) in the same transaction.
The ask stays as history either way: SELECT, INSERT, and UPDATE on the decision columns only;
never DELETE. One open ask per person per request (partial unique index). The reverse drops the
table and fails loudly while any row exists; the records placement pin becomes 24.
"""

from django.db import migrations

FORWARD = """
CREATE TABLE records.access_requests (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id          uuid NOT NULL REFERENCES records.requests (id),
  user_id             uuid NOT NULL REFERENCES records.users (id),
  reason              text NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  decided_at          timestamptz,
  decided_by_user_id  uuid REFERENCES records.users (id),
  decision            text CHECK (decision IN ('allowed', 'declined')),
  decision_note       text,
  CONSTRAINT access_requests_decided_together
    CHECK ((decided_at IS NULL) = (decision IS NULL))
);
CREATE UNIQUE INDEX access_requests_open_uniq
  ON records.access_requests (request_id, user_id) WHERE decided_at IS NULL;
CREATE INDEX access_requests_request_idx ON records.access_requests (request_id);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.access_requests TO portal_app;
    GRANT UPDATE (decided_at, decided_by_user_id, decision, decision_note)
      ON records.access_requests TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.access_requests;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0014: % access request(s) exist. Ledger rows are never '
      'deleted to make a reverse succeed.', blocked;
  END IF;
END
$$;
DROP TABLE records.access_requests;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0013_sent_mail_and_contacts")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
