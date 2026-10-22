"""The five additions owner approved on 2026-09-08. Additive only; schema.sql is untouched.

1. deadlines.rule_ref: which effective-dated rule version computed the row.
2. requests.sensitivity: public | internal | privileged, keys privacy controls.
3. records.users -> core.users: one uuid is one person across the portal.
4. SECURITY DEFINER on the schema's functions, so the triggers can move the
   cache columns that the app role is not allowed to write.
5. A unique index on sources.external_ref, so a re-delivered webhook cannot
   create a duplicate source.
"""

from django.db import migrations

FORWARD = """
ALTER TABLE records.deadlines
  ADD COLUMN IF NOT EXISTS rule_ref text;

ALTER TABLE records.requests
  ADD COLUMN IF NOT EXISTS sensitivity text NOT NULL DEFAULT 'internal'
    CONSTRAINT requests_sensitivity_check
    CHECK (sensitivity IN ('public', 'internal', 'privileged'));
CREATE INDEX IF NOT EXISTS requests_sensitivity_idx ON records.requests (sensitivity);

ALTER TABLE records.users
  ADD CONSTRAINT users_core_identity_fk
  FOREIGN KEY (id) REFERENCES core.users (id) DEFERRABLE INITIALLY DEFERRED;

ALTER FUNCTION records.recompute_request_current_status(uuid)
  SECURITY DEFINER SET search_path = records;
ALTER FUNCTION records.recompute_request_next_deadline(uuid)
  SECURITY DEFINER SET search_path = records;
ALTER FUNCTION records.next_tracking_number()
  SECURITY DEFINER SET search_path = records;

CREATE UNIQUE INDEX IF NOT EXISTS sources_external_ref_uniq
  ON records.sources (external_ref) WHERE external_ref IS NOT NULL;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT UPDATE (sensitivity) ON records.requests TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DROP INDEX IF EXISTS records.sources_external_ref_uniq;
ALTER FUNCTION records.next_tracking_number() SECURITY INVOKER RESET search_path;
ALTER FUNCTION records.recompute_request_next_deadline(uuid) SECURITY INVOKER RESET search_path;
ALTER FUNCTION records.recompute_request_current_status(uuid) SECURITY INVOKER RESET search_path;
ALTER TABLE records.users DROP CONSTRAINT IF EXISTS users_core_identity_fk;
DROP INDEX IF EXISTS records.requests_sensitivity_idx;
ALTER TABLE records.requests DROP COLUMN IF EXISTS sensitivity;
ALTER TABLE records.deadlines DROP COLUMN IF EXISTS rule_ref;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("records_db", "0002_privileges"),
        ("core", "0001_initial"),
    ]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
