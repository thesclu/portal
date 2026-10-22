"""Who may see a privileged request. A new table; additive.

`requests.sensitivity = 'privileged'` was a label until this part. From here a privileged request
is visible to the people with an active row in this table and to its requester of record, and to
nobody else, administrators included (DECISIONS, "Design v2"; BUILD_PLAN P9). The rule itself is
read in one place, `records/api/permissions.py`; this table is only what it reads.

  records.request_access
    request_id          the request shared
    user_id             the person it is shared with
    granted_by_user_id  who shared it; NULL only when a process did (none does today)
    note                why, in the granter's words
    granted_at          when
    revoked_at / revoked_by_user_id
                        access ends by being revoked, never by the row being deleted
                        (non-negotiable 6), so who could see a request at any past
                        moment stays answerable

The partial unique index allows one ACTIVE grant per person per request and any number of revoked
ones: sharing again after a revoke is a new row, and the old one stays as history. It also serves
the lookup the rule makes ("is there an active row for this request and this person").

Grants (0002's `ON ALL TABLES` covered only the tables that existed then -- BUILD_PLAN rule 4):
SELECT, INSERT, and UPDATE on (revoked_at, revoked_by_user_id) only. Never DELETE, and who was
granted, by whom and when are fixed at insert.

It refuses to run while any request is already privileged. Until now that word hid nothing, so a
request carrying it has no list; the moment this part's code runs, such a request would be visible
to nobody but its requester of record, and no screen would show that it exists. There is no right
person to put on its list from here. The refusal says what to do: make those requests `internal`,
migrate, and seal them again through the portal, which lists the person who seals them.

The reverse drops the table, and FAILS LOUDLY while any row exists, by an exception that names what
blocks it (the rule 0007 and 0009 follow). Dropping it with rows in it would do more than lose
history: every privileged request would lose its list at once. `schema.sql` is untouched and its
sha256 does not move; the records placement pin becomes 19 in the same commit.
"""

from django.db import migrations

FORWARD = """
DO $$
DECLARE
  sealed bigint;
BEGIN
  SELECT count(*) INTO sealed FROM records.requests WHERE sensitivity = 'privileged';
  IF sealed > 0 THEN
    RAISE EXCEPTION
      'cannot apply records_db.0010: % request(s) are already privileged and would be left with '
      'nobody on their access list. Set them to internal, migrate, then make them privileged '
      'again through the portal.',
      sealed;
  END IF;
END
$$;

CREATE TABLE records.request_access (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id          uuid NOT NULL REFERENCES records.requests (id),
  user_id             uuid NOT NULL REFERENCES records.users (id),
  granted_by_user_id  uuid REFERENCES records.users (id),
  note                text,
  granted_at          timestamptz NOT NULL DEFAULT now(),
  revoked_at          timestamptz,
  revoked_by_user_id  uuid REFERENCES records.users (id)
);
CREATE UNIQUE INDEX request_access_active_uniq
  ON records.request_access (request_id, user_id) WHERE revoked_at IS NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.request_access TO portal_app;
    GRANT UPDATE (revoked_at, revoked_by_user_id) ON records.request_access TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.request_access;
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0010: % access row(s) exist in records.request_access. '
      'Ledger rows are never deleted to make a reverse succeed.',
      blocked;
  END IF;
END
$$;

DROP TABLE records.request_access;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0009_source_notes")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
