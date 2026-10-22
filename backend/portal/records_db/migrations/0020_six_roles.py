"""Six roles: `records.users.role` admits staff_attorney, limited_attorney and intern (P20h).

NOT PURELY ADDITIVE, on the precedent of 0007 (BUILD_PLAN P20h; DECISIONS 37a, 42 and 43, owner's
ruling of 2026-10-17). schema.sql's `users.role` CHECK lists three roles. Distinct roles are
the point: a check written for `staff` denies an intern rather than letting one through, which a
role reused under a new name could not promise. So the CHECK is dropped and re-added with the six
values; nothing else changes and schema.sql itself is untouched (its sha256 does not move).

The constraint's name is the one Postgres gave it when schema.sql created the table, read from
`pg_constraint` on 2026-10-17, not assumed:

    users   users_role_check   CHECK ((role = ANY (ARRAY['staff', 'requester', 'admin'])))

`records.users` carries a deferred foreign key to `core.users` (0003), so a transaction that has just
written a person (the test suite's reversal tests do) holds pending trigger events, and Postgres
refuses to ALTER the table under them: `SET CONSTRAINTS ALL IMMEDIATE` checks them first.

It is dropped with NO `IF EXISTS`: a database holding it under another name fails loudly here
rather than ending with two CHECKs or none.

The reverse restores the three-value CHECK. It FAILS LOUDLY, by an explicit exception naming how
many people hold a new role, if any do. Rewriting a person's role to make a reverse succeed would
change what they may see, which is the one thing a migration must never do silently.
"""

from django.db import migrations

FORWARD = """
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE records.users DROP CONSTRAINT users_role_check;
ALTER TABLE records.users
  ADD CONSTRAINT users_role_check
  CHECK (role IN ('admin', 'staff', 'staff_attorney', 'limited_attorney', 'intern', 'requester'));
"""

BACKWARD = """
DO $$
DECLARE
  blocked bigint;
BEGIN
  SELECT count(*) INTO blocked FROM records.users
    WHERE role NOT IN ('staff', 'requester', 'admin');
  IF blocked > 0 THEN
    RAISE EXCEPTION
      'cannot reverse records_db.0020: % person(s) hold a role the original CHECK does not allow '
      '(staff_attorney, limited_attorney or intern). Change their roles first; a migration never '
      'rewrites what a person may see.',
      blocked;
  END IF;
END
$$;

SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE records.users DROP CONSTRAINT users_role_check;
ALTER TABLE records.users
  ADD CONSTRAINT users_role_check CHECK (role IN ('staff', 'requester', 'admin'));
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0019_own_access_requests")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
