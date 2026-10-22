"""Stored agency portal logins (BUILD_PLAN P19g, design 2c). One row per agency: the login as
typed, the password encrypted at rest with `PORTAL_SECRETS_KEY` (never stored in clear, never
written to the audit or activity log), and who changed it when. UPDATE on every column but the
agency; no DELETE: clearing a login blanks it and says who did. The placement pin becomes 27."""

from django.db import migrations

FORWARD = """
CREATE TABLE records.agency_logins (
  agency_id            uuid PRIMARY KEY REFERENCES records.agencies (id),
  login                text NOT NULL DEFAULT '',
  password_ciphertext  text NOT NULL DEFAULT '',
  notes                text,
  changed_at           timestamptz NOT NULL DEFAULT now(),
  changed_by_user_id   uuid REFERENCES records.users (id)
);

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'portal_app') THEN
    GRANT SELECT, INSERT ON records.agency_logins TO portal_app;
    GRANT UPDATE (login, password_ciphertext, notes, changed_at, changed_by_user_id)
      ON records.agency_logins TO portal_app;
  END IF;
END
$$;
"""

BACKWARD = """
DROP TABLE records.agency_logins;
"""


class Migration(migrations.Migration):
    dependencies = [("records_db", "0017_row_level_security")]
    operations = [migrations.RunSQL(sql=[FORWARD], reverse_sql=[BACKWARD])]
